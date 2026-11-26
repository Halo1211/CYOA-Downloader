//! Port of `network/throttle.py` (bandwidth window + per-domain request spacing).
//!
//! Bandwidth is *window accounting*, not a token bucket (preserved from Python):
//! the window starts without credit; after a sleep the window resets; otherwise the
//! window resets only when more than 1 s elapsed since the window start.
//! GUI speed callbacks are emitted by the caller before `consume` (Python order).

use std::collections::HashMap;
use std::sync::{Arc, Mutex as StdMutex};

use tokio::sync::Mutex;
use tokio::time::{Duration, Instant};
use tokio_util::sync::CancellationToken;

use crate::retry::DomainBackoff;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ThrottleError {
    #[error("download cancelled")]
    Cancelled,
}

async fn cancel_aware_sleep(seconds: f64, cancel: &CancellationToken) -> Result<(), ThrottleError> {
    let Ok(delay) = Duration::try_from_secs_f64(seconds.max(0.0)) else {
        return Ok(());
    };
    tokio::select! {
        biased;
        () = cancel.cancelled() => Err(ThrottleError::Cancelled),
        () = tokio::time::sleep(delay) => Ok(()),
    }
}

#[derive(Debug, Default)]
struct Window {
    last: Option<Instant>,
    bytes: u64,
}

/// Shared aggregate bandwidth limiter (clone to share). Limit unit: KB/s (1024 B/s).
#[derive(Clone, Debug)]
pub struct BandwidthThrottle {
    limit_kbps: Arc<StdMutex<f64>>,
    window: Arc<Mutex<Window>>,
}

impl BandwidthThrottle {
    pub fn new(limit_kbps: f64) -> Self {
        Self {
            limit_kbps: Arc::new(StdMutex::new(sanitize(limit_kbps))),
            window: Arc::new(Mutex::new(Window::default())),
        }
    }

    /// Live limit change; like Python it neither resets accounting nor interrupts a sleep.
    pub fn set_limit(&self, limit_kbps: f64) {
        let mut guard = self.limit_kbps.lock().unwrap_or_else(|e| e.into_inner());
        *guard = sanitize(limit_kbps);
    }

    pub fn limit(&self) -> f64 {
        *self.limit_kbps.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Python `_throttle_bandwidth(bytes)` (minus the GUI callback). The window lock is
    /// held across the sleep, serialising throttled transfers exactly as Python's lock does.
    pub async fn consume(
        &self,
        bytes: u64,
        cancel: &CancellationToken,
    ) -> Result<(), ThrottleError> {
        let limit = self.limit();
        if limit <= 0.0 {
            return Ok(());
        }
        let limit_bps = limit * 1024.0;
        let mut window = tokio::select! {
            biased;
            () = cancel.cancelled() => return Err(ThrottleError::Cancelled),
            window = self.window.lock() => window,
        };
        let now = Instant::now();
        let last = *window.last.get_or_insert(now);
        let elapsed = now.duration_since(last).as_secs_f64();
        window.bytes = window.bytes.saturating_add(bytes);
        let expected = window.bytes as f64 / limit_bps;
        let sleep_for = expected - elapsed;
        if sleep_for > 0.0 {
            cancel_aware_sleep(sleep_for, cancel).await?;
            window.last = Some(Instant::now());
            window.bytes = 0;
        } else if elapsed > 1.0 {
            window.last = Some(now);
            window.bytes = 0;
        }
        Ok(())
    }
}

fn sanitize(limit: f64) -> f64 {
    if limit.is_finite() { limit } else { 0.0 }
}

#[derive(Debug, Default)]
struct DomainState {
    backoff: HashMap<String, DomainBackoff>,
    last_slot: HashMap<String, Instant>,
}

/// Per-domain spacing + adaptive backoff (Python `_domain_throttle`,
/// `_domain_record_success`, `_domain_record_failure`). Key: lowercased netloc.
#[derive(Clone, Debug, Default)]
pub struct DomainThrottle {
    min_interval: f64,
    state: Arc<StdMutex<DomainState>>,
}

pub fn netloc(url: &str) -> Option<String> {
    let parsed = url::Url::parse(url).ok()?;
    let host = parsed.host_str()?;
    let mut netloc = String::new();
    if !parsed.username().is_empty() {
        netloc.push_str(parsed.username());
        if let Some(password) = parsed.password() {
            netloc.push(':');
            netloc.push_str(password);
        }
        netloc.push('@');
    }
    netloc.push_str(host);
    if let Some(port) = parsed.port() {
        netloc.push_str(&format!(":{port}"));
    }
    Some(netloc.to_lowercase())
}

impl DomainThrottle {
    pub fn new(min_interval_seconds: f64) -> Self {
        Self {
            min_interval: sanitize(min_interval_seconds).max(0.0),
            state: Arc::default(),
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, DomainState> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Reserve the next slot for the domain, then wait without holding any lock.
    pub async fn wait_turn(
        &self,
        url: &str,
        cancel: &CancellationToken,
    ) -> Result<(), ThrottleError> {
        let Some(domain) = netloc(url) else {
            return Ok(());
        };
        let now = Instant::now();
        let reserved_at = {
            let mut state = self.lock();
            let backoff = state
                .backoff
                .get(&domain)
                .map_or(0.0, |b| b.seconds.max(0.0));
            let interval = Duration::try_from_secs_f64(self.min_interval).unwrap_or_default();
            let backoff = Duration::try_from_secs_f64(backoff).unwrap_or_default();
            let mut reserved = now.max(now + backoff);
            if let Some(previous) = state.last_slot.get(&domain) {
                reserved = reserved.max(*previous + interval);
            }
            state.last_slot.insert(domain, reserved);
            reserved
        };
        let wait = reserved_at.saturating_duration_since(now).as_secs_f64();
        if wait > 0.0 {
            cancel_aware_sleep(wait, cancel).await?;
        }
        Ok(())
    }

    pub fn record_success(&self, url: &str) {
        if let Some(domain) = netloc(url)
            && let Some(entry) = self.lock().backoff.get_mut(&domain)
        {
            entry.success();
        }
    }

    /// Returns the new backoff seconds. `random` follows Python's random.random().
    pub fn record_failure(&self, url: &str, random: impl FnMut() -> f64) -> f64 {
        let Some(domain) = netloc(url) else {
            return 0.0;
        };
        self.lock()
            .backoff
            .entry(domain)
            .or_default()
            .failure(random)
    }
}

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used)]
mod tests {
    use super::*;

    #[tokio::test(start_paused = true)]
    async fn bandwidth_window_matches_python_accounting() {
        // limit 1 KB/s: first 2048 bytes expect 2 s, elapsed 0 -> sleep 2 s, window resets.
        let throttle = BandwidthThrottle::new(1.0);
        let cancel = CancellationToken::new();
        let start = Instant::now();
        throttle.consume(2048, &cancel).await.unwrap();
        assert_eq!(start.elapsed(), Duration::from_secs(2));
        // Next 512 bytes after reset: expected 0.5 s, elapsed 0 -> sleep 0.5 s.
        throttle.consume(512, &cancel).await.unwrap();
        assert_eq!(start.elapsed(), Duration::from_millis(2500));
        // Idle 3 s; 100 bytes: expected ~0.098 s < elapsed 3 s -> no sleep, window resets.
        tokio::time::advance(Duration::from_secs(3)).await;
        let before = Instant::now();
        throttle.consume(100, &cancel).await.unwrap();
        assert_eq!(before.elapsed(), Duration::ZERO);
        // Disabled limit never waits.
        throttle.set_limit(0.0);
        throttle.consume(1 << 30, &cancel).await.unwrap();
        assert_eq!(before.elapsed(), Duration::ZERO);
    }

    #[tokio::test(start_paused = true)]
    async fn bandwidth_sleep_is_cancellable() {
        let throttle = BandwidthThrottle::new(1.0);
        let cancel = CancellationToken::new();
        let child = cancel.clone();
        let task = tokio::spawn(async move { throttle.consume(1 << 20, &child).await });
        tokio::time::advance(Duration::from_millis(10)).await;
        cancel.cancel();
        assert_eq!(task.await.unwrap(), Err(ThrottleError::Cancelled));
    }

    #[tokio::test(start_paused = true)]
    async fn queued_bandwidth_throttling_is_cancellable() {
        let throttle = BandwidthThrottle::new(1.0);
        let cancel = CancellationToken::new();
        let _held_by_other_transfer = throttle.window.lock().await;
        let mut waiting = std::pin::pin!(throttle.consume(1024, &cancel));
        assert!(futures_util::poll!(&mut waiting).is_pending());
        cancel.cancel();
        assert!(matches!(
            tokio::time::timeout(Duration::from_millis(1), waiting).await,
            Ok(Err(ThrottleError::Cancelled))
        ));
    }

    #[tokio::test(start_paused = true)]
    async fn domain_slots_and_backoff() {
        let throttle = DomainThrottle::new(1.0);
        let cancel = CancellationToken::new();
        let start = Instant::now();
        throttle
            .wait_turn("https://A.test/x", &cancel)
            .await
            .unwrap();
        assert_eq!(start.elapsed(), Duration::ZERO);
        // Second request to the same netloc waits the 1 s minimum interval.
        throttle
            .wait_turn("https://a.test/y", &cancel)
            .await
            .unwrap();
        assert_eq!(start.elapsed(), Duration::from_secs(1));
        // Other domains are independent.
        throttle
            .wait_turn("https://b.test/", &cancel)
            .await
            .unwrap();
        assert_eq!(start.elapsed(), Duration::from_secs(1));
        // Failure backoff (random 0.5 -> 2 s) delays the next request by >= 2 s.
        assert_eq!(throttle.record_failure("https://c.test/", || 0.5), 2.0);
        let before = Instant::now();
        throttle
            .wait_turn("https://c.test/", &cancel)
            .await
            .unwrap();
        assert_eq!(before.elapsed(), Duration::from_secs(2));
        throttle.record_success("https://c.test/");
        assert_eq!(throttle.lock().backoff["c.test"].seconds, 1.0);
        assert_eq!(
            netloc("https://User:P@Host.test:8443/a").unwrap(),
            "user:p@host.test:8443"
        );
    }
}
