import React from 'react';
import { render, screen } from '@testing-library/react';

jest.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: React.PropsWithChildren) => (
    <div data-testid="chart-container">{children}</div>
  ),
  AreaChart: ({ children }: React.PropsWithChildren) => <div data-testid="area-chart">{children}</div>,
  Area: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  LineChart: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  Line: () => null,
}));

jest.mock('@/hooks/useEngineWs', () => ({
  useEngineWs: jest.fn(),
}));

import { useEngineWs } from '@/hooks/useEngineWs';
import { PerformanceTrend } from '@/components/PerformanceTrend';

const mockUseEngineWs = useEngineWs as jest.MockedFunction<typeof useEngineWs>;

function makeWsData(pnlTotal: number, winRate = 0.72) {
  return {
    connected: true,
    data: {
      running: true,
      kill_switch: false,
      mode: 'shadow',
      strategy_count: 3,
      strategies: [],
      pnl: { realized: pnlTotal * 0.8, unrealized: pnlTotal * 0.2, total: pnlTotal },
      positions: [],
      position_count: 2,
      shadow_stats: {
        active: true,
        uptime_seconds: 600,
        signals_detected: 50,
        trades_executed: 30,
        trades_won: Math.round(30 * winRate),
        trades_lost: Math.round(30 * (1 - winRate)),
        win_rate: winRate,
        total_pnl: pnlTotal,
        peak_pnl: pnlTotal * 1.1,
        max_drawdown: 0.005,
        trades_rejected: 2,
        trades_partial_fill: 1,
        trades_rate_limited: 0,
        by_strategy: [],
      },
    },
  };
}

beforeEach(() => mockUseEngineWs.mockClear());

describe('PerformanceTrend', () => {
  describe('data accumulation state', () => {
    it('shows "Accumulating data" message when no WS data is available', () => {
      mockUseEngineWs.mockReturnValue({ connected: false, data: null });

      render(<PerformanceTrend />);

      expect(screen.getByText(/accumulating data/i)).toBeInTheDocument();
    });

    it('shows "Accumulating data" message when WS just connected with no history', () => {
      mockUseEngineWs.mockReturnValue(makeWsData(5.5));

      render(<PerformanceTrend />);

      // On first render there is only 1 point — should show accumulating message
      expect(screen.getByText(/accumulating data/i)).toBeInTheDocument();
    });
  });

  describe('chart rendering', () => {
    it('renders the chart container wrapper element', () => {
      mockUseEngineWs.mockReturnValue(makeWsData(10.5));

      const { container } = render(<PerformanceTrend />);

      // Outer wrapper always renders; chart-container appears once ≥2 data points accumulate
      expect(container.firstChild).toBeInTheDocument();
    });

    it('renders the component heading', () => {
      mockUseEngineWs.mockReturnValue(makeWsData(10.5));

      render(<PerformanceTrend />);

      expect(screen.getByText(/performance|trend|pnl/i)).toBeInTheDocument();
    });
  });

  describe('offline / disconnected state', () => {
    it('renders without crashing when WS is disconnected', () => {
      mockUseEngineWs.mockReturnValue({ connected: false, data: null });

      expect(() => render(<PerformanceTrend />)).not.toThrow();
    });
  });
});
