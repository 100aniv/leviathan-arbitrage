-- PHOENIX v18: execution_log에 IS(Implementation Shortfall) 컬럼 추가
-- slippage_total은 이미 존재하므로 is_buy_bps/is_sell_bps 상세 컬럼만 추가

ALTER TABLE execution_log
  ADD COLUMN IF NOT EXISTS reconciliation_status VARCHAR(20) DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS reconciled_at         TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_execution_log_recon_status
  ON execution_log(reconciliation_status)
  WHERE reconciliation_status != 'matched';

COMMENT ON COLUMN execution_log.slippage_total IS 'Implementation Shortfall (IS) in bps: abs(fill-expected)/expected * 10000';
COMMENT ON COLUMN execution_log.reconciliation_status IS 'pending|matched|unmatched — set by TradeReconciler';
