/// Atomic kill switch — mirrors Python kill_switch.py (engine/src/risk/kill_switch.py).
///
/// TIER 1 (<1ms): AtomicBool halt flag — NO external dependency.
/// Every order submission path MUST check is_halted() before proceeding.
///
/// Provides:
///   halt_local()   — set halt flag (<0.01ms)
///   is_halted()    — check halt state (single atomic load)
///   clear_halt()   — reset halt flag (after full reconciliation)
///   PyKillSwitch   — Python-accessible class with trigger/reset

use pyo3::prelude::*;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Instant;

/// Global halt flag — module-level, same semantics as Python threading.Event.
static HALT_FLAG: AtomicBool = AtomicBool::new(false);

/// Set the global halt flag. <0.01ms. NO external dependency.
#[pyfunction]
pub fn halt_local() {
    HALT_FLAG.store(true, Ordering::SeqCst);
}

/// Check halt state. Every order submission MUST call this.
#[pyfunction]
pub fn is_halted() -> bool {
    HALT_FLAG.load(Ordering::SeqCst)
}

/// Clear halt flag. Only after full reconciliation.
#[pyfunction]
pub fn clear_halt() {
    HALT_FLAG.store(false, Ordering::SeqCst);
}

/// Per-instance kill switch with timing and state tracking.
/// Mirrors Python KillSwitch class.
#[pyclass(name = "KillSwitch")]
pub struct PyKillSwitch {
    triggered: bool,
    /// Per-instance halt flag (independent of global flag)
    instance_halt: Arc<AtomicBool>,
    #[allow(dead_code)]
    tier3_enabled: bool,
}

/// Timing breakdown from a trigger event.
#[pyclass(name = "KillSwitchEvent")]
#[derive(Clone, Debug)]
pub struct PyKillSwitchEvent {
    #[pyo3(get)]
    pub tier1_latency_ms: f64,
    #[pyo3(get)]
    pub triggered: bool,
    #[pyo3(get)]
    pub errors: Vec<String>,
}

#[pymethods]
impl PyKillSwitch {
    #[new]
    #[pyo3(signature = (tier3_enabled = true))]
    pub fn new(tier3_enabled: bool) -> Self {
        Self {
            triggered: false,
            instance_halt: Arc::new(AtomicBool::new(false)),
            tier3_enabled,
        }
    }

    /// Check halt state (both global and instance-local flags).
    pub fn is_halted(&self) -> bool {
        HALT_FLAG.load(Ordering::SeqCst) || self.instance_halt.load(Ordering::SeqCst)
    }

    /// Tier 1: set halt flag atomically. Returns latency in ms.
    /// Target: <1ms. Actual: <0.01ms (single atomic store).
    pub fn trigger_tier1(&mut self) -> PyResult<PyKillSwitchEvent> {
        let t_start = Instant::now();
        let mut errors = Vec::new();

        if self.triggered {
            errors.push("Already triggered".to_string());
            return Ok(PyKillSwitchEvent {
                tier1_latency_ms: 0.0,
                triggered: false,
                errors,
            });
        }

        // Set both global and instance halt flags
        HALT_FLAG.store(true, Ordering::SeqCst);
        self.instance_halt.store(true, Ordering::SeqCst);
        self.triggered = true;

        let latency_ms = t_start.elapsed().as_secs_f64() * 1000.0;

        Ok(PyKillSwitchEvent {
            tier1_latency_ms: latency_ms,
            triggered: true,
            errors,
        })
    }

    /// Reset kill switch. Only after full reconciliation.
    pub fn reset(&mut self) {
        HALT_FLAG.store(false, Ordering::SeqCst);
        self.instance_halt.store(false, Ordering::SeqCst);
        self.triggered = false;
    }

    /// Whether this instance has been triggered.
    pub fn was_triggered(&self) -> bool {
        self.triggered
    }
}

#[pymethods]
impl PyKillSwitchEvent {
    fn __repr__(&self) -> String {
        format!(
            "KillSwitchEvent(triggered={}, tier1_latency_ms={:.4}, errors={})",
            self.triggered,
            self.tier1_latency_ms,
            self.errors.len()
        )
    }
}
