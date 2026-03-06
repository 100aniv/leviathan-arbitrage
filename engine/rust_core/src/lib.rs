/// rust_core — PyO3 bindings for performance-critical engine components.
///
/// Exposes three sub-modules to Python:
///   - orderbook: BTreeMap-based L2 orderbook (<1μs updates)
///   - signal:    Spread calculator for cross-exchange arb (<5μs)
///   - kill_switch: AtomicBool halt flag (<1ms propagation)

use pyo3::prelude::*;

pub mod kill_switch;
pub mod orderbook;
pub mod signal;

#[pymodule]
fn rust_core(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    // Orderbook
    m.add_class::<orderbook::PyOrderBook>()?;

    // Signal processor
    m.add_class::<signal::PySpreadCalculator>()?;
    m.add_class::<signal::PyQuote>()?;
    m.add_function(wrap_pyfunction!(signal::compute_spread_pct, m)?)?;
    m.add_function(wrap_pyfunction!(signal::best_bid_ask_across, m)?)?;

    // Kill switch
    m.add_function(wrap_pyfunction!(kill_switch::halt_local, m)?)?;
    m.add_function(wrap_pyfunction!(kill_switch::is_halted, m)?)?;
    m.add_function(wrap_pyfunction!(kill_switch::clear_halt, m)?)?;
    m.add_class::<kill_switch::PyKillSwitch>()?;
    m.add_class::<kill_switch::PyKillSwitchEvent>()?;

    Ok(())
}
