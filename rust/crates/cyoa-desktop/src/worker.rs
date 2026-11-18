//! Background download worker: a dedicated thread owning a Tokio runtime.
//!
//! The UI never blocks: commands go in through a bounded channel, events come
//! back through a bounded channel that the UI drains with `try_recv`. Progress
//! events are dropped when the UI falls behind (`try_send`); lifecycle events
//! (`Started`/`Finished`) are always delivered.

use cyoa_engine::{
    pipeline::Progress,
    run::{RunError, RunOptions, run_download},
};
use std::{
    collections::{HashMap, VecDeque},
    sync::mpsc::{Receiver, SyncSender, TrySendError, sync_channel},
    thread::JoinHandle,
    time::{Duration, Instant},
};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

pub type JobId = u64;

#[derive(Debug)]
pub enum Command {
    Start(JobId, RunOptions),
    Cancel(JobId),
    CancelAll,
    Shutdown,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct JobSummary {
    pub written: Vec<String>,
    pub failed_assets: usize,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum JobResult {
    Done(JobSummary),
    Failed(String),
    Cancelled,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    Started {
        job: JobId,
    },
    Progress {
        job: JobId,
        done: usize,
        total: usize,
        name: String,
        ok: bool,
    },
    Finished {
        job: JobId,
        result: JobResult,
    },
}

/// Wakes the UI after an event is queued (egui `request_repaint` in the app).
pub type Waker = Box<dyn Fn() + Send + Sync>;

pub struct WorkerHandle {
    commands: mpsc::Sender<Command>,
    events: Receiver<Event>,
    thread: Option<JoinHandle<()>>,
}

const COMMAND_CAPACITY: usize = 1024;
const EVENT_CAPACITY: usize = 256;

impl WorkerHandle {
    pub fn spawn(waker: Waker) -> std::io::Result<Self> {
        let (cmd_tx, cmd_rx) = mpsc::channel(COMMAND_CAPACITY);
        let (ev_tx, ev_rx) = sync_channel(EVENT_CAPACITY);
        let thread = std::thread::Builder::new()
            .name("cyoa-worker".into())
            .spawn(move || {
                let runtime = match tokio::runtime::Builder::new_multi_thread()
                    .worker_threads(3)
                    .enable_all()
                    .build()
                {
                    Ok(rt) => rt,
                    Err(e) => {
                        eprintln!("worker runtime failed: {e}");
                        return;
                    }
                };
                runtime.block_on(worker_loop(cmd_rx, ev_tx, waker));
                runtime.shutdown_timeout(Duration::from_secs(2));
            })?;
        Ok(Self {
            commands: cmd_tx,
            events: ev_rx,
            thread: Some(thread),
        })
    }

    /// Non-blocking; returns false if the worker is gone or the queue is full.
    pub fn send(&self, cmd: Command) -> bool {
        self.commands.try_send(cmd).is_ok()
    }

    pub fn try_recv(&self) -> Option<Event> {
        self.events.try_recv().ok()
    }

    /// Cancel everything and wait (bounded) for the worker thread to exit.
    pub fn shutdown(&mut self, timeout: Duration) {
        let _ = self.commands.try_send(Command::Shutdown);
        let deadline = Instant::now() + timeout;
        // Keep draining so a blocked lifecycle send cannot deadlock shutdown.
        while self.thread.as_ref().is_some_and(|t| !t.is_finished()) && Instant::now() < deadline {
            while self.events.try_recv().is_ok() {}
            std::thread::sleep(Duration::from_millis(10));
        }
        if let Some(t) = self.thread.take()
            && t.is_finished()
        {
            let _ = t.join();
        }
    }
}

impl Drop for WorkerHandle {
    fn drop(&mut self) {
        self.shutdown(Duration::from_secs(3));
    }
}

fn deliver(tx: &SyncSender<Event>, waker: &Waker, ev: Event, lossy: bool) -> bool {
    let ok = if lossy {
        match tx.try_send(ev) {
            Ok(()) | Err(TrySendError::Full(_)) => true,
            Err(TrySendError::Disconnected(_)) => false,
        }
    } else {
        tx.send(ev).is_ok()
    };
    waker();
    ok
}

async fn worker_loop(
    mut commands: mpsc::Receiver<Command>,
    events: SyncSender<Event>,
    waker: Waker,
) {
    let mut queue: VecDeque<(JobId, RunOptions)> = VecDeque::new();
    let mut tokens: HashMap<JobId, CancellationToken> = HashMap::new();
    let shutdown = CancellationToken::new();
    let events = std::sync::Arc::new(events);
    let waker = std::sync::Arc::new(waker);
    let mut running: Option<tokio::task::JoinHandle<()>> = None;

    loop {
        // Start the next queued job when idle (Python processes the queue one at a time).
        if running.as_ref().is_none_or(|h| h.is_finished()) {
            running = None;
            if let Some((job, opts)) = queue.pop_front() {
                let token = tokens.entry(job).or_default().clone();
                if token.is_cancelled() {
                    tokens.remove(&job);
                    let blocking_events = events.clone();
                    let w = waker.clone();
                    let _ = tokio::task::spawn_blocking(move || {
                        deliver(
                            &blocking_events,
                            &w,
                            Event::Finished {
                                job,
                                result: JobResult::Cancelled,
                            },
                            false,
                        )
                    })
                    .await;
                    continue;
                }
                running = Some(tokio::spawn(run_job(
                    job,
                    opts,
                    token,
                    events.clone(),
                    waker.clone(),
                )));
            }
        }

        let cmd = if let Some(handle) = running.as_mut() {
            tokio::select! {
                _ = handle => { running = None; continue; }
                cmd = commands.recv() => cmd,
            }
        } else {
            commands.recv().await
        };
        match cmd {
            Some(Command::Start(job, opts)) => {
                tokens.insert(job, CancellationToken::new());
                queue.push_back((job, opts));
            }
            Some(Command::Cancel(job)) => {
                if let Some(t) = tokens.get(&job) {
                    t.cancel();
                }
            }
            Some(Command::CancelAll) => tokens.values().for_each(CancellationToken::cancel),
            Some(Command::Shutdown) | None => {
                shutdown.cancel();
                tokens.values().for_each(CancellationToken::cancel);
                if let Some(h) = running.take() {
                    let _ = tokio::time::timeout(Duration::from_secs(2), h).await;
                }
                return;
            }
        }
        tokens.retain(|id, _| queue.iter().any(|(q, _)| q == id) || running.is_some());
    }
}

async fn run_job(
    job: JobId,
    opts: RunOptions,
    cancel: CancellationToken,
    events: std::sync::Arc<SyncSender<Event>>,
    waker: std::sync::Arc<Waker>,
) {
    let send_blocking = |ev: Event| {
        let events = events.clone();
        let waker = waker.clone();
        tokio::task::spawn_blocking(move || deliver(&events, &waker, ev, false))
    };
    let _ = send_blocking(Event::Started { job }).await;

    let (ptx, mut prx) = mpsc::channel::<Progress>(64);
    let fwd_events = events.clone();
    let fwd_waker = waker.clone();
    let forward = tokio::spawn(async move {
        while let Some(p) = prx.recv().await {
            if let Progress::AssetDone {
                done,
                total,
                ok,
                name,
            } = p
            {
                deliver(
                    &fwd_events,
                    &fwd_waker,
                    Event::Progress {
                        job,
                        done,
                        total,
                        name,
                        ok,
                    },
                    true,
                );
            }
        }
    });
    let result = run_download(&opts, cancel.clone(), Some(ptx)).await;
    let _ = forward.await;
    let result = match result {
        Ok(report) => JobResult::Done(JobSummary {
            written: report
                .written_paths
                .iter()
                .map(|p| p.display().to_string())
                .collect(),
            failed_assets: report.failed_assets.len(),
        }),
        Err(RunError::Cancelled) => JobResult::Cancelled,
        Err(_) if cancel.is_cancelled() => JobResult::Cancelled,
        Err(e) => JobResult::Failed(e.to_string()),
    };
    let _ = send_blocking(Event::Finished { job, result }).await;
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    fn wait_finished(w: &WorkerHandle, job: JobId, within: Duration) -> Option<JobResult> {
        let end = Instant::now() + within;
        while Instant::now() < end {
            while let Some(ev) = w.try_recv() {
                if let Event::Finished { job: j, result } = ev
                    && j == job
                {
                    return Some(result);
                }
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        None
    }

    #[test]
    fn invalid_url_job_fails_without_blocking() {
        let mut w = WorkerHandle::spawn(Box::new(|| {})).unwrap();
        let opts = RunOptions {
            url: "not-a-url".into(),
            output_dir: std::env::temp_dir(),
            ..RunOptions::default()
        };
        assert!(w.send(Command::Start(1, opts)));
        let r = wait_finished(&w, 1, Duration::from_secs(10)).unwrap();
        assert!(matches!(r, JobResult::Failed(_)), "{r:?}");
        w.shutdown(Duration::from_secs(3));
    }

    #[test]
    fn cancelled_queued_job_finishes_promptly() {
        // Unroutable TEST-NET address: the first job hangs on connect until cancelled.
        let mut w = WorkerHandle::spawn(Box::new(|| {})).unwrap();
        let hang = RunOptions {
            url: "http://192.0.2.1/project.json".into(),
            output_dir: std::env::temp_dir(),
            ..RunOptions::default()
        };
        assert!(w.send(Command::Start(1, hang.clone())));
        assert!(w.send(Command::Start(2, hang)));
        std::thread::sleep(Duration::from_millis(200));
        let started = Instant::now();
        assert!(w.send(Command::CancelAll));
        assert!(wait_finished(&w, 1, Duration::from_secs(5)).is_some());
        assert_eq!(
            wait_finished(&w, 2, Duration::from_secs(5)),
            Some(JobResult::Cancelled)
        );
        assert!(started.elapsed() < Duration::from_secs(5));
        w.shutdown(Duration::from_secs(3));
    }
}
