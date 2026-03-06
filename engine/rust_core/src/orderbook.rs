/// High-performance L2 orderbook backed by BTreeMap.
///
/// Mirrors Python OrderBook (engine/src/core/order_book.py) exactly.
/// Target: <1μs per update (vs ~100μs Python).
///
/// Prices stored as (mantissa: i64, exponent: i8) fixed-point pairs
/// to avoid float rounding while keeping arithmetic fast.

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use std::collections::BTreeMap;

/// Fixed-point decimal: value = mantissa * 10^exponent
/// Supports up to 18 significant digits — sufficient for crypto prices.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct FixedDecimal {
    /// Scaled integer: price * SCALE
    scaled: i64,
}

const SCALE: i64 = 1_000_000_000; // 9 decimal places
const SCALE_F: f64 = 1_000_000_000.0;

impl FixedDecimal {
    pub fn zero() -> Self {
        Self { scaled: 0 }
    }

    pub fn from_f64(v: f64) -> Self {
        Self {
            scaled: (v * SCALE_F).round() as i64,
        }
    }

    pub fn to_f64(self) -> f64 {
        self.scaled as f64 / SCALE_F
    }

    pub fn is_zero(self) -> bool {
        self.scaled == 0
    }

    pub fn is_positive(self) -> bool {
        self.scaled > 0
    }
}

impl std::ops::Add for FixedDecimal {
    type Output = Self;
    fn add(self, rhs: Self) -> Self {
        Self {
            scaled: self.scaled + rhs.scaled,
        }
    }
}

impl std::ops::Sub for FixedDecimal {
    type Output = Self;
    fn sub(self, rhs: Self) -> Self {
        Self {
            scaled: self.scaled - rhs.scaled,
        }
    }
}

impl std::ops::Mul for FixedDecimal {
    type Output = Self;
    fn mul(self, rhs: Self) -> Self {
        // (a/S) * (b/S) = ab/S^2 — divide by SCALE after multiply
        let result = (self.scaled as i128 * rhs.scaled as i128 / SCALE as i128) as i64;
        Self { scaled: result }
    }
}

impl std::ops::Div for FixedDecimal {
    type Output = Self;
    fn div(self, rhs: Self) -> Self {
        // (a/S) / (b/S) = a/b — multiply numerator by SCALE
        let result = (self.scaled as i128 * SCALE as i128 / rhs.scaled as i128) as i64;
        Self { scaled: result }
    }
}

/// Parse a string like "12345.678" into FixedDecimal.
/// Returns Err if the string is not a valid decimal number.
fn parse_price(s: &str) -> Result<FixedDecimal, String> {
    let trimmed = s.trim();
    // Handle empty
    if trimmed.is_empty() {
        return Err("empty price string".to_string());
    }
    // Parse via f64 for simplicity — crypto prices fit in f64 mantissa
    let v: f64 = trimmed
        .parse()
        .map_err(|_| format!("invalid decimal: {s}"))?;
    Ok(FixedDecimal::from_f64(v))
}

/// BTreeMap-based L2 orderbook.
///
/// Bids sorted descending (best = max key), asks ascending (best = min key).
#[pyclass(name = "OrderBook")]
pub struct PyOrderBook {
    pub symbol: String,
    pub exchange: String,
    /// bid price → quantity (all positive)
    bids: BTreeMap<FixedDecimal, FixedDecimal>,
    /// ask price → quantity (all positive)
    asks: BTreeMap<FixedDecimal, FixedDecimal>,
}

#[pymethods]
impl PyOrderBook {
    #[new]
    pub fn new(symbol: String, exchange: String) -> Self {
        Self {
            symbol,
            exchange,
            bids: BTreeMap::new(),
            asks: BTreeMap::new(),
        }
    }

    /// Replace entire orderbook with snapshot. Zero-qty levels are ignored.
    pub fn apply_snapshot(
        &mut self,
        bids: Vec<(String, String)>,
        asks: Vec<(String, String)>,
    ) -> PyResult<()> {
        self.bids.clear();
        self.asks.clear();
        for (p_str, q_str) in &bids {
            let p = parse_price(p_str).map_err(|e| PyValueError::new_err(e))?;
            let q = parse_price(q_str).map_err(|e| PyValueError::new_err(e))?;
            if q.is_positive() {
                self.bids.insert(p, q);
            }
        }
        for (p_str, q_str) in &asks {
            let p = parse_price(p_str).map_err(|e| PyValueError::new_err(e))?;
            let q = parse_price(q_str).map_err(|e| PyValueError::new_err(e))?;
            if q.is_positive() {
                self.asks.insert(p, q);
            }
        }
        Ok(())
    }

    /// Apply incremental updates. qty == "0" removes the level.
    pub fn apply_delta(
        &mut self,
        bid_updates: Vec<(String, String)>,
        ask_updates: Vec<(String, String)>,
    ) -> PyResult<()> {
        for (p_str, q_str) in &bid_updates {
            let p = parse_price(p_str).map_err(|e| PyValueError::new_err(e))?;
            let q = parse_price(q_str).map_err(|e| PyValueError::new_err(e))?;
            if q.is_zero() {
                self.bids.remove(&p);
            } else {
                self.bids.insert(p, q);
            }
        }
        for (p_str, q_str) in &ask_updates {
            let p = parse_price(p_str).map_err(|e| PyValueError::new_err(e))?;
            let q = parse_price(q_str).map_err(|e| PyValueError::new_err(e))?;
            if q.is_zero() {
                self.asks.remove(&p);
            } else {
                self.asks.insert(p, q);
            }
        }
        Ok(())
    }

    /// Highest bid price as float, or None if empty.
    pub fn best_bid(&self) -> Option<f64> {
        self.bids.keys().next_back().map(|p| p.to_f64())
    }

    /// Lowest ask price as float, or None if empty.
    pub fn best_ask(&self) -> Option<f64> {
        self.asks.keys().next().map(|p| p.to_f64())
    }

    /// Absolute bid-ask spread, or None.
    pub fn spread(&self) -> Option<f64> {
        let bid = self.bids.keys().next_back()?;
        let ask = self.asks.keys().next()?;
        Some((*ask - *bid).to_f64())
    }

    /// Relative spread as fraction of best bid, or None.
    pub fn spread_pct(&self) -> Option<f64> {
        let bid = self.bids.keys().next_back()?;
        let ask = self.asks.keys().next()?;
        if bid.is_zero() {
            return None;
        }
        Some((*ask - *bid).to_f64() / bid.to_f64())
    }

    /// Depth-weighted mid price across top N levels.
    /// Raises ValueError if either side is empty or zero-quantity.
    #[pyo3(signature = (depth = 5))]
    pub fn depth_weighted_mid_price(&self, depth: usize) -> PyResult<f64> {
        let sorted_bids: Vec<_> = self.bids.iter().rev().take(depth).collect();
        let sorted_asks: Vec<_> = self.asks.iter().take(depth).collect();

        if sorted_bids.is_empty() || sorted_asks.is_empty() {
            return Err(PyValueError::new_err(
                "OrderBook is empty — cannot compute mid price",
            ));
        }

        let bid_qty_total: f64 = sorted_bids.iter().map(|(_, q)| q.to_f64()).sum();
        let ask_qty_total: f64 = sorted_asks.iter().map(|(_, q)| q.to_f64()).sum();

        if bid_qty_total == 0.0 || ask_qty_total == 0.0 {
            return Err(PyValueError::new_err(
                "Zero total quantity in orderbook levels",
            ));
        }

        let bid_vwap: f64 = sorted_bids
            .iter()
            .map(|(p, q)| p.to_f64() * q.to_f64())
            .sum::<f64>()
            / bid_qty_total;

        let ask_vwap: f64 = sorted_asks
            .iter()
            .map(|(p, q)| p.to_f64() * q.to_f64())
            .sum::<f64>()
            / ask_qty_total;

        Ok((bid_vwap + ask_vwap) / 2.0)
    }

    /// Return quantity at a specific price level. Returns 0.0 if absent.
    pub fn volume_at_price(&self, price: f64, side: &str) -> PyResult<f64> {
        let p = FixedDecimal::from_f64(price);
        match side {
            "bid" => Ok(self.bids.get(&p).map(|q| q.to_f64()).unwrap_or(0.0)),
            "ask" => Ok(self.asks.get(&p).map(|q| q.to_f64()).unwrap_or(0.0)),
            _ => Err(PyTypeError::new_err(format!(
                "Invalid side '{side}': must be 'bid' or 'ask'"
            ))),
        }
    }

    /// Binance-style CRC32 checksum over top-5 bid/ask levels.
    pub fn compute_checksum(&self) -> u32 {
        let top_bids: Vec<_> = self.bids.iter().rev().take(5).collect();
        let top_asks: Vec<_> = self.asks.iter().take(5).collect();

        let mut parts: Vec<String> = Vec::with_capacity(10);
        for (p, q) in &top_bids {
            parts.push(format!("{:.9}@{:.9}", p.to_f64(), q.to_f64()));
        }
        for (p, q) in &top_asks {
            parts.push(format!("{:.9}@{:.9}", p.to_f64(), q.to_f64()));
        }
        let payload = parts.join("|");
        crc32_ieee(payload.as_bytes())
    }

    /// Validate orderbook integrity against expected CRC32.
    pub fn validate_checksum(&self, expected: u32) -> bool {
        self.compute_checksum() == expected
    }

    /// Number of bid levels.
    pub fn bid_count(&self) -> usize {
        self.bids.len()
    }

    /// Number of ask levels.
    pub fn ask_count(&self) -> usize {
        self.asks.len()
    }
}

/// CRC32/ISO-HDLC (same as Python zlib.crc32 & 0xFFFFFFFF).
fn crc32_ieee(data: &[u8]) -> u32 {
    let mut crc: u32 = 0xFFFF_FFFF;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 {
            if crc & 1 != 0 {
                crc = (crc >> 1) ^ 0xEDB8_8320;
            } else {
                crc >>= 1;
            }
        }
    }
    crc ^ 0xFFFF_FFFF
}
