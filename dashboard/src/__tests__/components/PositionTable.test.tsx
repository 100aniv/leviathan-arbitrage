import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

jest.mock('@/hooks/useApi', () => ({
  useApi: jest.fn(),
}));

import { useApi } from '@/hooks/useApi';
import { PositionTable } from '@/components/PositionTable';

const mockUseApi = useApi as jest.MockedFunction<typeof useApi>;

const mockPositions = [
  {
    strategy_id: 'tri-arb',
    exchange_id: 'Binance',
    symbol: 'BTC/USDT',
    side: 'LONG',
    quantity: 0.05,
    entry_price: 64850,
    mark_price: 64900,
    unrealized_pnl: 12.5,
    realized_pnl: 0,
  },
  {
    strategy_id: 'kim-arb',
    exchange_id: 'Upbit',
    symbol: 'ETH/USDT',
    side: 'SHORT',
    quantity: 0.8,
    entry_price: 3480,
    mark_price: 3490,
    unrealized_pnl: -5.0,
    realized_pnl: 2.0,
  },
];

describe('PositionTable', () => {
  beforeEach(() => mockUseApi.mockClear());

  it('renders heading and position count', () => {
    mockUseApi.mockReturnValue({
      data: mockPositions,
      error: undefined,
      isLoading: false,
      isValidating: false,
      mutate: jest.fn(),
    } as ReturnType<typeof useApi>);

    render(<PositionTable />);

    expect(screen.getByText('Open Positions')).toBeInTheDocument();
    expect(screen.getByText('2 positions')).toBeInTheDocument();
  });

  it('renders rows with position data', () => {
    mockUseApi.mockReturnValue({
      data: mockPositions,
      error: undefined,
      isLoading: false,
      isValidating: false,
      mutate: jest.fn(),
    } as ReturnType<typeof useApi>);

    render(<PositionTable />);

    expect(screen.getByText('tri-arb')).toBeInTheDocument();
    expect(screen.getByText('BTC/USDT')).toBeInTheDocument();
    expect(screen.getByText('Binance')).toBeInTheDocument();
    expect(screen.getByText('ETH/USDT')).toBeInTheDocument();
  });

  it('falls back to mock data when API returns empty array', () => {
    mockUseApi.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
      isValidating: false,
      mutate: jest.fn(),
    } as ReturnType<typeof useApi>);

    render(<PositionTable />);

    // Built-in MOCK has 5 positions
    expect(screen.getByText('5 positions')).toBeInTheDocument();
  });

  it('sorts by a column when header is clicked', async () => {
    const user = userEvent.setup();
    mockUseApi.mockReturnValue({
      data: mockPositions,
      error: undefined,
      isLoading: false,
      isValidating: false,
      mutate: jest.fn(),
    } as ReturnType<typeof useApi>);

    render(<PositionTable />);

    // Default sort is unrealized_pnl desc — click Strategy header
    const strategyHeader = screen.getByText('Strategy');
    await user.click(strategyHeader);

    // Shows descending indicator on Strategy column
    expect(screen.getByText(/Strategy/)).toHaveTextContent('Strategy↓');
  });

  it('toggles sort direction when clicking the active column', async () => {
    const user = userEvent.setup();
    mockUseApi.mockReturnValue({
      data: mockPositions,
      error: undefined,
      isLoading: false,
      isValidating: false,
      mutate: jest.fn(),
    } as ReturnType<typeof useApi>);

    render(<PositionTable />);

    // uPnL is already the active sort key (desc) — click it to toggle to asc
    const pnlHeader = screen.getByText('uPnL');
    await user.click(pnlHeader);

    expect(screen.getByText(/uPnL/)).toHaveTextContent('uPnL↑');
  });

  it('shows loading skeleton when data is loading', () => {
    mockUseApi.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
      isValidating: false,
      mutate: jest.fn(),
    } as ReturnType<typeof useApi>);

    render(<PositionTable />);

    expect(screen.queryByText('Open Positions')).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('shows error state with retry button', () => {
    const mutate = jest.fn();
    mockUseApi.mockReturnValue({
      data: undefined,
      error: new Error('Network error'),
      isLoading: false,
      isValidating: false,
      mutate,
    } as ReturnType<typeof useApi>);

    render(<PositionTable />);

    expect(screen.getByText('Failed to load positions')).toBeInTheDocument();
    expect(screen.getByText('Retry')).toBeInTheDocument();
  });

  it('calls mutate when retry is clicked', async () => {
    const user = userEvent.setup();
    const mutate = jest.fn();
    mockUseApi.mockReturnValue({
      data: undefined,
      error: new Error('fail'),
      isLoading: false,
      isValidating: false,
      mutate,
    } as ReturnType<typeof useApi>);

    render(<PositionTable />);
    await user.click(screen.getByText('Retry'));

    expect(mutate).toHaveBeenCalledTimes(1);
  });
});
