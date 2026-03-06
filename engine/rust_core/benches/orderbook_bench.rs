use criterion::{black_box, criterion_group, criterion_main, Criterion, BenchmarkId};
use rust_core::orderbook::PyOrderBook;

fn make_snapshot(n: usize) -> (Vec<(String, String)>, Vec<(String, String)>) {
    let bids: Vec<_> = (0..n)
        .map(|i| {
            let price = 50000.0 - i as f64 * 0.01;
            let qty = 0.1 + i as f64 * 0.001;
            (format!("{price:.8}"), format!("{qty:.8}"))
        })
        .collect();
    let asks: Vec<_> = (0..n)
        .map(|i| {
            let price = 50000.01 + i as f64 * 0.01;
            let qty = 0.1 + i as f64 * 0.001;
            (format!("{price:.8}"), format!("{qty:.8}"))
        })
        .collect();
    (bids, asks)
}

fn make_delta(n: usize) -> (Vec<(String, String)>, Vec<(String, String)>) {
    let bids: Vec<_> = (0..n)
        .map(|i| {
            let price = 50000.0 - i as f64 * 0.01;
            // Alternate: update quantity or remove
            if i % 3 == 0 {
                (format!("{price:.8}"), "0.00000000".to_string())
            } else {
                let qty = 0.5 + i as f64 * 0.001;
                (format!("{price:.8}"), format!("{qty:.8}"))
            }
        })
        .collect();
    let asks: Vec<_> = (0..n)
        .map(|i| {
            let price = 50000.01 + i as f64 * 0.01;
            if i % 3 == 0 {
                (format!("{price:.8}"), "0.00000000".to_string())
            } else {
                let qty = 0.5 + i as f64 * 0.001;
                (format!("{price:.8}"), format!("{qty:.8}"))
            }
        })
        .collect();
    (bids, asks)
}

fn bench_apply_snapshot(c: &mut Criterion) {
    let mut group = c.benchmark_group("orderbook/apply_snapshot");
    for n in [10, 50, 100] {
        let (bids, asks) = make_snapshot(n);
        group.bench_with_input(BenchmarkId::from_parameter(n), &n, |b, _| {
            b.iter(|| {
                let mut ob = PyOrderBook::new("BTCUSDT".into(), "binance".into());
                ob.apply_snapshot(black_box(bids.clone()), black_box(asks.clone()))
                    .unwrap();
                black_box(&ob);
            });
        });
    }
    group.finish();
}

fn bench_apply_delta(c: &mut Criterion) {
    let (snap_bids, snap_asks) = make_snapshot(100);
    let (delta_bids, delta_asks) = make_delta(20);

    c.bench_function("orderbook/apply_delta_20_levels", |b| {
        let mut ob = PyOrderBook::new("BTCUSDT".into(), "binance".into());
        ob.apply_snapshot(snap_bids.clone(), snap_asks.clone())
            .unwrap();
        b.iter(|| {
            ob.apply_delta(
                black_box(delta_bids.clone()),
                black_box(delta_asks.clone()),
            )
            .unwrap();
        });
    });
}

fn bench_best_bid_ask(c: &mut Criterion) {
    let (bids, asks) = make_snapshot(100);
    let mut ob = PyOrderBook::new("BTCUSDT".into(), "binance".into());
    ob.apply_snapshot(bids, asks).unwrap();

    c.bench_function("orderbook/best_bid", |b| {
        b.iter(|| black_box(ob.best_bid()));
    });

    c.bench_function("orderbook/best_ask", |b| {
        b.iter(|| black_box(ob.best_ask()));
    });

    c.bench_function("orderbook/spread", |b| {
        b.iter(|| black_box(ob.spread()));
    });

    c.bench_function("orderbook/spread_pct", |b| {
        b.iter(|| black_box(ob.spread_pct()));
    });
}

fn bench_depth_weighted_mid(c: &mut Criterion) {
    let (bids, asks) = make_snapshot(100);
    let mut ob = PyOrderBook::new("BTCUSDT".into(), "binance".into());
    ob.apply_snapshot(bids, asks).unwrap();

    c.bench_function("orderbook/depth_weighted_mid_price_depth5", |b| {
        b.iter(|| black_box(ob.depth_weighted_mid_price(5).unwrap()));
    });
}

fn bench_checksum(c: &mut Criterion) {
    let (bids, asks) = make_snapshot(20);
    let mut ob = PyOrderBook::new("BTCUSDT".into(), "binance".into());
    ob.apply_snapshot(bids, asks).unwrap();

    c.bench_function("orderbook/compute_checksum", |b| {
        b.iter(|| black_box(ob.compute_checksum()));
    });
}

criterion_group!(
    benches,
    bench_apply_snapshot,
    bench_apply_delta,
    bench_best_bid_ask,
    bench_depth_weighted_mid,
    bench_checksum,
);
criterion_main!(benches);
