//! Retry policy (port of `network/retry_policy.py`, `network/sessions.py` retry config,
//! `download/image_pipeline.py:688-759`). Design: Sol (2026-11-18), saved by Claude.
//!
//! Preserve separate retry owners: media statuses must not multiply transport retries.
//! Transport: 3 retries after the initial request; backoff 0, 2, 4 seconds.
//! Media: 4 direct attempts, 1 page-like attempt, 0 after gallery prehandling.
//! Domain backoff persists across requests, including the terminal failure.
//! urllib3 caps its own Retry-After parser at 21600 s and backoff at 120 s; the media
//! helper bypasses that parser: its Retry-After is uncapped; domain backoff caps at 300 s.

use std::future::Future;

pub const MEDIA_STATUS_RETRY_CODES: [u16; 5] = [429, 500, 502, 503, 504];
pub const TRANSPORT_RETRIES: u8 = 3;

tokio::task_local! {
    static CALLER_STATUS_RETRIES: bool;
}

/// Scope the entire request future; nested scopes restore the previous value.
pub async fn caller_status_retry_scope<F: Future>(future: F) -> F::Output {
    CALLER_STATUS_RETRIES.scope(true, future).await
}

pub fn status_retries_owned_by_caller() -> bool {
    CALLER_STATUS_RETRIES
        .try_with(|value| *value)
        .unwrap_or(false)
}

/// urllib3 status eligibility, before checking the request's retry budget.
/// 413 with Retry-After remains transport-owned even inside the media scope.
pub fn transport_status_retry(method: &str, status: u16, has_retry_after: bool) -> bool {
    if MEDIA_STATUS_RETRY_CODES.contains(&status) && status_retries_owned_by_caller() {
        return false;
    }
    let allowed = method.eq_ignore_ascii_case("GET") || method.eq_ignore_ascii_case("HEAD");
    allowed && (MEDIA_STATUS_RETRY_CODES.contains(&status) || (status == 413 && has_retry_after))
}

/// Consecutive failure history length AFTER increment; no urllib3 jitter configured.
pub fn transport_backoff(history_len: u32) -> f64 {
    match history_len {
        0 | 1 => 0.0,
        2..=7 => f64::from(2_u32.pow(history_len - 1)),
        _ => 120.0,
    }
}

/// One budget per transport request, shared across connect/read/status errors.
#[derive(Debug, Default)]
pub struct TransportRetryBudget {
    used: u8,
}

impl TransportRetryBudget {
    pub fn next_backoff(&mut self) -> Option<f64> {
        if self.used >= TRANSPORT_RETRIES {
            return None;
        }
        self.used += 1;
        Some(transport_backoff(u32::from(self.used)))
    }
}

/// Python float(value), then date parsing, then finite/nonnegative filtering.
/// `now` is Unix seconds. No Retry-After cap applies on the media path.
pub fn retry_after_seconds(value: Option<&str>, now: f64) -> f64 {
    let Some(value) = value else {
        return 0.0;
    };
    let value = value.trim();
    let seconds = numeric_seconds(value)
        .or_else(|| imf_fixdate(value).map(|timestamp| timestamp as f64 - now))
        .unwrap_or(0.0);
    if seconds.is_finite() {
        seconds.max(0.0)
    } else {
        0.0
    }
}

fn numeric_seconds(value: &str) -> Option<f64> {
    // Python float accepts underscores only between decimal digits.
    let bytes = value.as_bytes();
    for (index, byte) in bytes.iter().enumerate() {
        if *byte == b'_'
            && (index == 0
                || !bytes[index - 1].is_ascii_digit()
                || !bytes.get(index + 1).is_some_and(u8::is_ascii_digit))
        {
            return None;
        }
    }
    value.replace('_', "").parse().ok()
}

/// RFC 7231 IMF-fixdate. Weekday/date disagreement is ignored, as in Python.
fn imf_fixdate(value: &str) -> Option<i64> {
    if value.len() != 29 || !value.is_ascii() {
        return None;
    }
    if &value[3..5] != ", "
        || &value[7..8] != " "
        || &value[11..12] != " "
        || &value[16..17] != " "
        || &value[19..20] != ":"
        || &value[22..23] != ":"
        || &value[25..29] != " GMT"
        || !["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].contains(&&value[..3])
    {
        return None;
    }
    let month = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    .iter()
    .position(|month| *month == &value[8..11])?;
    let number = |text: &str| -> Option<i64> {
        if text.bytes().all(|byte| byte.is_ascii_digit()) {
            text.parse().ok()
        } else {
            None
        }
    };
    let year = number(&value[12..16])?;
    let day = number(&value[5..7])?;
    let hour = number(&value[17..19])?;
    let minute = number(&value[20..22])?;
    let second = number(&value[23..25])?;
    if year == 0 || hour > 23 || minute > 59 || second > 59 {
        return None;
    }
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let mut lengths = [31_i64, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if leap {
        lengths[1] = 29;
    }
    if day < 1 || day > lengths[month] {
        return None;
    }
    let previous_year = year - 1;
    let days = previous_year * 365 + previous_year / 4 - previous_year / 100
        + previous_year / 400
        + lengths[..month].iter().sum::<i64>()
        + day
        - 1
        - 719_162;
    Some(days * 86_400 + hour * 3600 + minute * 60 + second)
}

/// One instance per lowercased netloc, shared across assets.
/// RNG contract matches random.random(): a finite value in [0, 1).
#[derive(Debug, Default)]
pub struct DomainBackoff {
    pub seconds: f64,
    pub failures: u64,
}

impl DomainBackoff {
    pub fn failure(&mut self, mut random: impl FnMut() -> f64) -> f64 {
        self.failures = self.failures.saturating_add(1);
        let base = if self.seconds <= 0.0 {
            2.0
        } else {
            (self.seconds * 2.0).min(300.0)
        };
        let jitter = base * 0.25 * (2.0 * random() - 1.0);
        self.seconds = (base + jitter).clamp(0.1, 300.0);
        self.seconds
    }

    pub fn success(&mut self) {
        self.seconds = (self.seconds / 2.0).max(0.0);
        self.failures = self.failures.saturating_sub(1);
    }
}

#[derive(Debug)]
pub struct MediaRetryPolicy {
    pub attempts: usize,
    wait_time: f64,
}

impl MediaRetryPolicy {
    pub fn new(gallery_pretried: bool, page_like: bool, wait_time: f64) -> Self {
        let attempts = if gallery_pretried {
            0
        } else if page_like {
            1
        } else {
            4
        };
        Self {
            attempts,
            wait_time,
        }
    }

    /// Zero-based attempt. Record the domain failure even when this returns None.
    /// Close the response and admission lease BEFORE awaiting the delay.
    pub fn status_delay(
        &self,
        attempt: usize,
        status: u16,
        backoff: f64,
        retry_after: Option<&str>,
        now: f64,
    ) -> Option<f64> {
        if !MEDIA_STATUS_RETRY_CODES.contains(&status) || attempt.saturating_add(1) >= self.attempts
        {
            return None;
        }
        let delay = backoff.max(retry_after_seconds(retry_after, now));
        Some(if status == 429 {
            delay.max(self.wait_time)
        } else {
            delay
        })
    }

    /// ConnectionError / non-exhausted RequestException outer-loop delays.
    pub fn exception_delay(&self, attempt: usize) -> Option<f64> {
        if attempt.saturating_add(1) >= self.attempts {
            None
        } else {
            Some((10 * attempt.saturating_add(1).min(3)) as f64)
        }
    }
}

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn retry_after_matches_python_fixtures() {
        for value in [None, Some("garbage"), Some("nan"), Some("inf"), Some("-10")] {
            assert_eq!(retry_after_seconds(value, 1000.0), 0.0);
        }
        assert_eq!(retry_after_seconds(Some("2.5"), 1000.0), 2.5);
        assert_eq!(retry_after_seconds(Some("1_000"), 0.0), 1000.0);
        assert_eq!(retry_after_seconds(Some("90000"), 0.0), 90_000.0);
        assert_eq!(
            retry_after_seconds(Some("Thu, 01 Jan 1970 00:18:40 GMT"), 1000.0),
            120.0
        );
        assert_eq!(
            retry_after_seconds(Some("Thu, 01 Jan 1970 00:15:00 GMT"), 1000.0),
            0.0
        );
        assert_eq!(
            imf_fixdate("Tue, 29 Feb 2000 00:00:00 GMT"),
            Some(951_782_400)
        );
        assert_eq!(imf_fixdate("Mon, 29 Feb 1900 00:00:00 GMT"), None);
        assert_eq!(imf_fixdate("Sun, 31 Apr 2022 00:00:00 GMT"), None);
        assert_eq!(imf_fixdate("\u{2603}"), None);
    }

    #[tokio::test]
    async fn ownership_is_nested_and_restored() {
        assert!(!status_retries_owned_by_caller());
        caller_status_retry_scope(async {
            for status in MEDIA_STATUS_RETRY_CODES {
                assert!(!transport_status_retry("GET", status, true));
            }
            assert!(transport_status_retry("GET", 413, true));
            caller_status_retry_scope(async {
                assert!(status_retries_owned_by_caller());
            })
            .await;
            assert!(status_retries_owned_by_caller());
        })
        .await;
        assert!(transport_status_retry("HEAD", 503, false));
        assert!(!transport_status_retry("POST", 503, true));
        assert!(!status_retries_owned_by_caller());
    }

    #[test]
    fn attempts_backoff_and_status_specific_wait_match_python() {
        let mut transport = TransportRetryBudget::default();
        assert_eq!(transport.next_backoff(), Some(0.0));
        assert_eq!(transport.next_backoff(), Some(2.0));
        assert_eq!(transport.next_backoff(), Some(4.0));
        assert_eq!(transport.next_backoff(), None);
        assert_eq!(transport_backoff(1000), 120.0);

        let policy = MediaRetryPolicy::new(false, false, 10.0);
        let mut backoff = DomainBackoff::default();
        for (attempt, expected) in [2.0, 4.0, 8.0, 16.0].into_iter().enumerate() {
            let seconds = backoff.failure(|| 0.5);
            assert_eq!(seconds, expected);
            assert_eq!(
                policy.status_delay(attempt, 503, seconds, None, 0.0),
                if attempt < 3 { Some(expected) } else { None }
            );
        }
        assert_eq!(policy.status_delay(0, 429, 2.0, None, 0.0), Some(10.0));
        assert_eq!(policy.status_delay(0, 503, 2.0, Some("7"), 0.0), Some(7.0));
        assert_eq!(policy.status_delay(0, 404, 2.0, None, 0.0), None);
        assert_eq!(policy.exception_delay(2), Some(30.0));
        assert_eq!(MediaRetryPolicy::new(false, true, 0.0).attempts, 1);
        assert_eq!(MediaRetryPolicy::new(true, false, 0.0).attempts, 0);

        let mut jitter = DomainBackoff::default();
        assert_eq!(jitter.failure(|| 0.0), 1.5);
        jitter.seconds = 300.0;
        assert_eq!(jitter.failure(|| 0.0), 225.0);
        assert_eq!(jitter.failure(|| 0.999), 300.0);
        jitter.success();
        assert_eq!(jitter.seconds, 150.0);
        jitter.seconds = 0.001;
        assert_eq!(jitter.failure(|| 0.0), 0.1);
    }
}
