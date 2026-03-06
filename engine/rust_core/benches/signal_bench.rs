use criterion::{black_box, criterion_group, criterion_main, Criterion, BenchmarkId};
use rust_core::signal::PySpreadCalculator;

/// Make quotes where exchange_0 has best ask and exchange_N has best bid (arb opportunity).
fn make_arb_quotes(n: usize) -> Vec<(String, f64, f64)> {
    let mut quotes: Vec<(String, f64, f64)> = (0..n)
        .map(|i| {
            let bid = 49990.0 + i as f64 * 0.1;
            let ask = bid + 2.0;
            (format!("exchange_{i}"), bid, ask)
        })
        .collect();
    // Make exchange_0 have lowest ask (buy here)
    quotes[0] = ("exchange_0".to_string(), 49985.0, 49988.0);
    // Make exchange_last have highest bid (sell here)
    quotes[n - 1] = (format!("exchange_{}", n - 1), 50010.0, 50012.0);
    quotes
}

fn bench_process_bulk(c: &mut Criterion) {
    let mut group = c.benchmark_group("signal/process_bulk");

    for n_symbols in [1, 10, 50] {
        let symbol_quotes: Vec<(String, Vec<(String, f64, f64)>)> = (0..n_symbols)
            .map(|i| {
                let symbol = format!("PAIR_{i}");
                let quotes = make_arb_quotes(4);
                (symbol, quotes)
            })
            .collect();

        group.bench_with_input(
            BenchmarkId::new("symbols", n_symbols),
            &n_symbols,
            |b, _| {
                let mut calc = PySpreadCalculator::new(0.0001, 0.0);
                b.iter(|| {
                    black_box(calc.process_bulk(black_box(symbol_quotes.clone())));
                });
            },
        );
    }
    group.finish();
}

fn bench_spread_pct(c: &mut Criterion) {
    c.bench_function("signal/compute_spread_pct", |b| {
        b.iter(|| {
            black_box(rust_core::signal::compute_spread_pct(
                black_box(49988.0),
                black_box(49999.0),
            ))
        });
    });
}

fn bench_best_bid_ask_across(c: &mut Criterion) {
    // We can't use PyRef directly in benchmarks (no GIL), so test process_bulk instead
    let mut group = c.benchmark_group("signal/process_bulk_2_exchanges");

    let symbol_quotes: Vec<(String, Vec<(String, f64, f64)>)> = vec![(
        "BTCUSDT".to_string(),
        vec![
            ("binance".to_string(), 50010.0, 50012.0),
            ("bitget".to_string(), 49985.0, 49988.0),
        ],
    )];

    group.bench_function("2_exchanges", |b| {
        let mut calc = PySpreadCalculator::new(0.0001, 0.0);
        b.iter(|| {
            black_box(calc.process_bulk(black_box(symbol_quotes.clone())));
        });
    });
    group.finish();
}

criterion_group!(
    benches,
    bench_process_bulk,
    bench_spread_pct,
    bench_best_bid_ask_across,
);
criterion_main!(benches);
