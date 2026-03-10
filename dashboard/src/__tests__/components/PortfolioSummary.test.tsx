import React from 'react';
import { render, screen } from '@testing-library/react';

jest.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: React.PropsWithChildren) => (
    <div data-testid="chart-container">{children}</div>
  ),
  AreaChart: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  Area: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
}));

jest.mock('@/hooks/useEngineWs', () => ({
  useEngineWs: jest.fn(),
}));

jest.mock('@/hooks/useApi', () => ({
  useApi: jest.fn(),
}));

import { useEngineWs } from '@/hooks/useEngineWs';
import { useApi } from '@/hooks/useApi';
import { PortfolioSummary } from '@/components/PortfolioSummary';

const mockUseEngineWs = useEngineWs as jest.MockedFunction<typeof useEngineWs>;
const mockUseApi = useApi as jest.MockedFunction<typeof useApi>;

const mockExchangeStatus = {
  binance: { exchange_id: 'binance', connected: true, latency_ms: 12, orderbook_depth: 20, symbols_count: 50, last_update: '', balance: { USDT: 5000 } },
  upbit:   { exchange_id: 'upbit',   connected: true, latency_ms: 24, orderbook_depth: 20, symbols_count: 30, last_update: '', balance: { USDT: 3000 } },
};

function makeWsData(overrides = {}) {
  return {
    connected: true,
    data: {
      running: true,
      kill_switch: false,
      mode: 'shadow',
      strategy_count: 3,
      strategies: [],
      pnl: { realized: 10.5, unrealized: 2.5, total: 13.0 },
      positions: [],
      position_count: 4,
      ...overrides,
    },
  };
}

beforeEach(() => {
  mockUseEngineWs.mockClear();
  mockUseApi.mockClear();
});

describe('PortfolioSummary', () => {
  describe('status badge', () => {
    it('shows RUNNING badge when engine is running', () => {
      mockUseEngineWs.mockReturnValue(makeWsData());
      mockUseApi.mockReturnValue({ data: mockExchangeStatus, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<PortfolioSummary />);

      expect(screen.getByText('● RUNNING')).toBeInTheDocument();
    });

    it('shows STOPPED badge when engine is not running', () => {
      mockUseEngineWs.mockReturnValue(makeWsData({ running: false }));
      mockUseApi.mockReturnValue({ data: mockExchangeStatus, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<PortfolioSummary />);

      expect(screen.getByText('● STOPPED')).toBeInTheDocument();
    });

    it('shows KILL SWITCH ACTIVE badge when kill_switch is true', () => {
      mockUseEngineWs.mockReturnValue(makeWsData({ kill_switch: true }));
      mockUseApi.mockReturnValue({ data: mockExchangeStatus, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<PortfolioSummary />);

      expect(screen.getByText(/KILL/i)).toBeInTheDocument();
    });
  });

  describe('KPI cards', () => {
    it('renders all four KPI card labels', () => {
      mockUseEngineWs.mockReturnValue(makeWsData());
      mockUseApi.mockReturnValue({ data: mockExchangeStatus, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<PortfolioSummary />);

      expect(screen.getByText(/Total Balance/i)).toBeInTheDocument();
      expect(screen.getByText(/Today PnL/i)).toBeInTheDocument();
      expect(screen.getByText(/Total PnL/i)).toBeInTheDocument();
      expect(screen.getByText(/Active Positions/i)).toBeInTheDocument();
    });

    it('renders active position count from WS data', () => {
      mockUseEngineWs.mockReturnValue(makeWsData({ position_count: 7 }));
      mockUseApi.mockReturnValue({ data: mockExchangeStatus, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<PortfolioSummary />);

      expect(screen.getByText('7')).toBeInTheDocument();
    });

    it('renders dash placeholders when WS data is null (offline)', () => {
      mockUseEngineWs.mockReturnValue({ connected: false, data: null });
      mockUseApi.mockReturnValue({ data: undefined, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<PortfolioSummary />);

      const dashes = screen.getAllByText('—');
      expect(dashes.length).toBeGreaterThanOrEqual(1);
    });

    it('aggregates Total Balance from exchange balances', () => {
      mockUseEngineWs.mockReturnValue(makeWsData());
      mockUseApi.mockReturnValue({ data: mockExchangeStatus, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<PortfolioSummary />);

      // binance 5000 + upbit 3000 = $8,000
      expect(screen.getByText(/8[,.]?000|8000/)).toBeInTheDocument();
    });
  });

  describe('ExchangeStatusBar', () => {
    it('renders exchange pills for connected exchanges', () => {
      mockUseEngineWs.mockReturnValue(makeWsData());
      mockUseApi.mockReturnValue({ data: mockExchangeStatus, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<PortfolioSummary />);

      expect(screen.getByText(/binance/i)).toBeInTheDocument();
      expect(screen.getByText(/upbit/i)).toBeInTheDocument();
    });

    it('does not render exchange bar when exchange data is unavailable', () => {
      mockUseEngineWs.mockReturnValue(makeWsData());
      mockUseApi.mockReturnValue({ data: undefined, error: undefined, isLoading: true, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<PortfolioSummary />);

      expect(screen.queryByText(/binance/i)).not.toBeInTheDocument();
    });
  });
});
