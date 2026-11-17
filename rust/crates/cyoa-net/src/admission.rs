//! Shared HTTP admission (port of `network/budget.py`). Lower numeric priorities run first;
//! FIFO within a priority. A saturated host never blocks waiters for other hosts.
//!
//! Keep the lease alive until the response body is consumed or closed.
//! Redirects must be handled with `RequestLease::redirect`.
//! Design: Sol (sol_review, 2026-11-18); saved and verified by Claude.

use std::collections::HashMap;
use std::sync::{Arc, LazyLock, Mutex, MutexGuard};

use tokio::sync::Notify;
use tokio_util::sync::CancellationToken;
use url::{Host, Url};

pub const HTTP_GLOBAL_LIMIT: usize = 20;
pub const HTTP_HOST_LIMIT: usize = 12;
pub const PRIORITY_FOREGROUND: i32 = 10;
pub const PRIORITY_BACKGROUND: i32 = 20;

pub static HTTP_BUDGET: LazyLock<RequestBudget> = LazyLock::new(RequestBudget::default);

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum AdmissionError {
    #[error("request cancelled")]
    Cancelled,
    #[error("request URL must contain a valid HTTP(S) host")]
    InvalidUrl,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdmissionStats {
    pub in_flight: usize,
    pub peak: usize,
    pub per_host: HashMap<String, usize>,
    pub waiting: usize,
    pub global_limit: usize,
    pub host_limit: usize,
}

#[derive(Debug)]
struct Waiter {
    host: String,
    priority: i32,
    notify: Notify,
}

#[derive(Debug, Default)]
struct State {
    in_flight: usize,
    peak: usize,
    hosts: HashMap<String, usize>,
    // Vector position is the FIFO ticket.
    waiters: Vec<Arc<Waiter>>,
}

#[derive(Debug)]
struct Inner {
    total: usize,
    per_host: usize,
    state: Mutex<State>,
}

impl Inner {
    fn lock(&self) -> MutexGuard<'_, State> {
        match self.state.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        }
    }

    fn wake_waiters(state: &State) {
        // Each waiter owns its Notify; notify_one stores a permit if not yet registered.
        for waiter in &state.waiters {
            waiter.notify.notify_one();
        }
    }

    fn release(&self, host: &str) {
        let mut state = self.lock();
        state.in_flight = state.in_flight.saturating_sub(1);
        if let Some(count) = state.hosts.get_mut(host) {
            *count = count.saturating_sub(1);
            if *count == 0 {
                state.hosts.remove(host);
            }
        }
        Self::wake_waiters(&state);
    }
}

#[derive(Debug, Clone)]
pub struct RequestBudget {
    inner: Arc<Inner>,
}

impl Default for RequestBudget {
    fn default() -> Self {
        Self::new(HTTP_GLOBAL_LIMIT, HTTP_HOST_LIMIT)
    }
}

impl RequestBudget {
    pub fn new(total: usize, per_host: usize) -> Self {
        Self {
            inner: Arc::new(Inner {
                total: total.max(1),
                per_host: per_host.max(1),
                state: Mutex::new(State::default()),
            }),
        }
    }

    pub fn snapshot(&self) -> AdmissionStats {
        let state = self.inner.lock();
        AdmissionStats {
            in_flight: state.in_flight,
            peak: state.peak,
            per_host: state.hosts.clone(),
            waiting: state.waiters.len(),
            global_limit: self.inner.total,
            host_limit: self.inner.per_host,
        }
    }

    pub async fn acquire(
        &self,
        url: &str,
        priority: i32,
        cancel: &CancellationToken,
    ) -> Result<RequestLease, AdmissionError> {
        if cancel.is_cancelled() {
            return Err(AdmissionError::Cancelled);
        }
        let host = host_key(url)?;
        let waiter = Arc::new(Waiter {
            host: host.clone(),
            priority,
            notify: Notify::new(),
        });
        let registration = Registration {
            inner: Arc::clone(&self.inner),
            waiter: Arc::clone(&waiter),
        };
        {
            let mut state = self.inner.lock();
            state.waiters.push(Arc::clone(&waiter));
            Inner::wake_waiters(&state);
        }
        loop {
            if cancel.is_cancelled() {
                return Err(AdmissionError::Cancelled);
            }
            let notified = waiter.notify.notified();
            let admitted = {
                let mut state = self.inner.lock();
                let first = if state.in_flight < self.inner.total {
                    state
                        .waiters
                        .iter()
                        .enumerate()
                        .filter(|(_, candidate)| {
                            state.hosts.get(&candidate.host).copied().unwrap_or(0)
                                < self.inner.per_host
                        })
                        .min_by_key(|(index, candidate)| (candidate.priority, *index))
                        .map(|(index, _)| index)
                } else {
                    None
                };
                match first {
                    Some(index)
                        if Arc::ptr_eq(&state.waiters[index], &waiter)
                            && !cancel.is_cancelled() =>
                    {
                        state.waiters.remove(index);
                        state.in_flight += 1;
                        *state.hosts.entry(host.clone()).or_default() += 1;
                        state.peak = state.peak.max(state.in_flight);
                        Inner::wake_waiters(&state);
                        true
                    }
                    _ => false,
                }
            };
            if admitted {
                drop(registration);
                let lease = RequestLease {
                    budget: self.clone(),
                    host,
                };
                if cancel.is_cancelled() {
                    drop(lease);
                    return Err(AdmissionError::Cancelled);
                }
                return Ok(lease);
            }
            tokio::select! {
                biased;
                () = cancel.cancelled() => return Err(AdmissionError::Cancelled),
                () = notified => {}
            }
        }
    }
}

fn host_key(value: &str) -> Result<String, AdmissionError> {
    let url = Url::parse(value).map_err(|_| AdmissionError::InvalidUrl)?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err(AdmissionError::InvalidUrl);
    }
    match url.host() {
        Some(Host::Domain(domain)) => {
            let host = domain.trim_end_matches('.').to_ascii_lowercase();
            if host.is_empty() {
                Err(AdmissionError::InvalidUrl)
            } else {
                Ok(host)
            }
        }
        Some(Host::Ipv4(address)) => Ok(address.to_string()),
        Some(Host::Ipv6(address)) => Ok(address.to_string()),
        None => Err(AdmissionError::InvalidUrl),
    }
}

/// Removes queued work when its acquire future is cancelled or dropped.
struct Registration {
    inner: Arc<Inner>,
    waiter: Arc<Waiter>,
}

impl Drop for Registration {
    fn drop(&mut self) {
        let mut state = self.inner.lock();
        if let Some(index) = state
            .waiters
            .iter()
            .position(|candidate| Arc::ptr_eq(candidate, &self.waiter))
        {
            state.waiters.remove(index);
        }
        Inner::wake_waiters(&state);
    }
}

/// A non-cloneable lease. Dropping it releases exactly one admission slot.
#[derive(Debug)]
#[must_use = "retain the lease until the response body is closed"]
pub struct RequestLease {
    budget: RequestBudget,
    host: String,
}

impl RequestLease {
    pub fn host(&self) -> &str {
        &self.host
    }

    pub fn release(self) {
        drop(self);
    }

    /// Close/drop the old response first. Releasing first avoids deadlock with a
    /// global limit of one. The target must already have passed redirect/SSRF checks.
    pub async fn redirect(
        self,
        target: &str,
        priority: i32,
        cancel: &CancellationToken,
    ) -> Result<Self, AdmissionError> {
        let budget = self.budget.clone();
        drop(self);
        budget.acquire(target, priority, cancel).await
    }
}

impl Drop for RequestLease {
    fn drop(&mut self) {
        self.budget.inner.release(&self.host);
    }
}

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used)]
mod tests {
    use super::*;
    use futures_util::poll;

    #[tokio::test(start_paused = true)]
    async fn default_limits_are_enforced_and_drop_releases() {
        let budget = RequestBudget::default();
        let cancel = CancellationToken::new();
        let mut leases = Vec::new();
        for _ in 0..12 {
            leases.push(
                budget
                    .acquire("https://a.test/", 10, &cancel)
                    .await
                    .unwrap(),
            );
        }
        let mut host_waiter = Box::pin(budget.acquire("https://a.test/", 10, &cancel));
        assert!(poll!(host_waiter.as_mut()).is_pending());
        for _ in 0..8 {
            leases.push(
                budget
                    .acquire("https://b.test/", 10, &cancel)
                    .await
                    .unwrap(),
            );
        }
        let mut global_waiter = Box::pin(budget.acquire("https://c.test/", 10, &cancel));
        assert!(poll!(global_waiter.as_mut()).is_pending());
        let stats = budget.snapshot();
        assert_eq!(stats.in_flight, 20);
        assert_eq!(stats.per_host["a.test"], 12);
        assert_eq!(stats.waiting, 2);
        drop(global_waiter);
        drop(host_waiter);
        drop(leases);
        let stats = budget.snapshot();
        assert_eq!(stats.in_flight, 0);
        assert!(stats.per_host.is_empty());
        assert_eq!(stats.waiting, 0);
        assert_eq!(stats.peak, 20);
    }

    #[tokio::test(start_paused = true)]
    async fn priority_then_fifo_controls_admission() {
        let budget = RequestBudget::new(1, 1);
        let cancel = CancellationToken::new();
        let held = budget
            .acquire("https://a.test/", 10, &cancel)
            .await
            .unwrap();
        let mut background = Box::pin(budget.acquire("https://a.test/", 20, &cancel));
        let mut first = Box::pin(budget.acquire("https://a.test/", 10, &cancel));
        let mut second = Box::pin(budget.acquire("https://a.test/", 10, &cancel));
        assert!(poll!(background.as_mut()).is_pending());
        assert!(poll!(first.as_mut()).is_pending());
        assert!(poll!(second.as_mut()).is_pending());
        drop(held);
        assert!(poll!(background.as_mut()).is_pending());
        assert!(poll!(second.as_mut()).is_pending());
        let first_lease = first.await.unwrap();
        assert!(poll!(second.as_mut()).is_pending());
        drop(first_lease);
        assert!(poll!(background.as_mut()).is_pending());
        let second_lease = second.await.unwrap();
        drop(second_lease);
        drop(background.await.unwrap());
        assert_eq!(budget.snapshot().in_flight, 0);
    }

    #[tokio::test(start_paused = true)]
    async fn saturated_host_does_not_block_another_host() {
        let budget = RequestBudget::new(2, 1);
        let cancel = CancellationToken::new();
        let held = budget
            .acquire("https://HOST.test:443/", 10, &cancel)
            .await
            .unwrap();
        let mut saturated = Box::pin(budget.acquire("http://host.test.:8080/", 0, &cancel));
        assert!(poll!(saturated.as_mut()).is_pending());
        let other = budget
            .acquire("https://other.test/", 20, &cancel)
            .await
            .unwrap();
        assert_eq!(budget.snapshot().in_flight, 2);
        assert!(poll!(saturated.as_mut()).is_pending());
        drop(other);
        drop(held);
        drop(saturated.await.unwrap());
    }

    #[tokio::test(start_paused = true)]
    async fn cancellation_and_future_drop_remove_waiters() {
        let budget = RequestBudget::new(1, 1);
        let root = CancellationToken::new();
        let owner = root.child_token();
        let held = budget.acquire("https://a.test/", 10, &root).await.unwrap();
        let mut cancelled = Box::pin(budget.acquire("https://b.test/", 10, &owner));
        assert!(poll!(cancelled.as_mut()).is_pending());
        owner.cancel();
        assert!(matches!(cancelled.await, Err(AdmissionError::Cancelled)));
        assert_eq!(budget.snapshot().waiting, 0);
        assert_eq!(budget.snapshot().in_flight, 1);
        let mut abandoned = Box::pin(budget.acquire("https://c.test/", 10, &root));
        assert!(poll!(abandoned.as_mut()).is_pending());
        drop(abandoned);
        assert_eq!(budget.snapshot().waiting, 0);
        drop(held);
    }

    #[tokio::test(start_paused = true)]
    async fn redirect_releases_origin_and_waits_for_target() {
        let budget = RequestBudget::new(2, 1);
        let cancel = CancellationToken::new();
        let origin = budget
            .acquire("https://a.test/", 10, &cancel)
            .await
            .unwrap();
        let target = budget
            .acquire("https://b.test/", 10, &cancel)
            .await
            .unwrap();
        let mut redirected = Box::pin(origin.redirect("https://b.test/next", 10, &cancel));
        assert!(poll!(redirected.as_mut()).is_pending());
        assert!(!budget.snapshot().per_host.contains_key("a.test"));
        assert_eq!(budget.snapshot().in_flight, 1);
        drop(target);
        let redirected = redirected.await.unwrap();
        assert_eq!(redirected.host(), "b.test");
        drop(redirected);
        assert_eq!(budget.snapshot().in_flight, 0);
    }

    #[tokio::test(start_paused = true)]
    async fn task_abort_and_panic_release_leases() {
        let budget = RequestBudget::new(1, 1);
        let worker_budget = budget.clone();
        let (ready_tx, ready_rx) = tokio::sync::oneshot::channel();
        let worker = tokio::spawn(async move {
            let cancel = CancellationToken::new();
            let _lease = worker_budget
                .acquire("https://a.test/", 10, &cancel)
                .await
                .unwrap();
            ready_tx.send(()).unwrap();
            std::future::pending::<()>().await;
        });
        ready_rx.await.unwrap();
        worker.abort();
        assert!(worker.await.unwrap_err().is_cancelled());
        assert_eq!(budget.snapshot().in_flight, 0);
        let worker_budget = budget.clone();
        let worker = tokio::spawn(async move {
            let cancel = CancellationToken::new();
            let _lease = worker_budget
                .acquire("https://a.test/", 10, &cancel)
                .await
                .unwrap();
            panic!("fixture panic");
        });
        assert!(worker.await.unwrap_err().is_panic());
        assert_eq!(budget.snapshot().in_flight, 0);
    }

    #[tokio::test(start_paused = true)]
    async fn already_cancelled_and_invalid_urls_never_acquire() {
        let budget = RequestBudget::new(1, 1);
        let cancel = CancellationToken::new();
        assert_eq!(
            budget
                .acquire("ftp://a.test/", 10, &cancel)
                .await
                .unwrap_err(),
            AdmissionError::InvalidUrl
        );
        cancel.cancel();
        assert_eq!(
            budget
                .acquire("https://a.test/", 10, &cancel)
                .await
                .unwrap_err(),
            AdmissionError::Cancelled
        );
        assert_eq!(budget.snapshot().in_flight, 0);
    }
}
