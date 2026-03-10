import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// Mock recharts before importing the component to avoid ResizeObserver issues
jest.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: React.PropsWithChildren) => (
    <div data-testid="chart-container">{children}</div>
  ),
  LineChart: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
}));

jest.mock('@/hooks/useApi', () => ({
  useApi: jest.fn(),
}));

import { useApi } from '@/hooks/useApi';
import { PnLChart } from '@/components/PnLChart';

const mockUseApi = useApi as jest.MockedFunction<typeof useApi>;

describe('PnLChart', () => {
  beforeEach(() => mockUseApi.mockClear());

  it('renders the chart with live data', () => {
    mockUseApi.mockReturnValue({
      data: { total_pnl: 150, realized_pnl: 100, unrealized_pnl: 50 },
      error: undefined,
      isLoading: false,
      mutate: jest.fn(),
    } as ReturnType<typeof useApi>);

    render(<PnLChart />);

    expect(screen.getByText('PnL Curve')).toBeInTheDocument();
    expect(screen.getByText('● LIVE')).toBeInTheDocument();
    expect(screen.getByTestId('chart-container')).toBeInTheDocument();
  });

  it('shows summary cards with total, realized, unrealized labels', () => {
    mockUseApi.mockReturnValue({
      data: { total_pnl: 200, realized_pnl: 150, unrealized_pnl: 50 },
      error: undefined,
      isLoading: false,
      mutate: jest.fn(),
    } as ReturnType<typeof useApi>);

    render(<PnLChart />);

    expect(screen.getByText('Total')).toBeInTheDocument();
    expect(screen.getByText('Realized')).toBeInTheDocument();
    expect(screen.getByText('Unrealized')).toBeInTheDocument();
  });

  it('renders seed data chart when API returns no data', () => {
    mockUseApi.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
      mutate: jest.fn(),
    } as ReturnType<typeof useApi>);

    render(<PnLChart />);

    // Chart container still renders
    expect(screen.getByTestId('chart-container')).toBeInTheDocument();
  });

  it('shows error state with retry button', () => {
    const mutate = jest.fn();
    mockUseApi.mockReturnValue({
      data: undefined,
      error: new Error('Connection failed'),
      isLoading: false,
      mutate,
    } as ReturnType<typeof useApi>);

    render(<PnLChart />);

    expect(screen.getByText('Connection error')).toBeInTheDocument();
    expect(screen.getByText('Retry')).toBeInTheDocument();
  });

  it('calls mutate when retry is clicked', async () => {
    const user = userEvent.setup();
    const mutate = jest.fn();
    mockUseApi.mockReturnValue({
      data: undefined,
      error: new Error('fail'),
      isLoading: false,
      mutate,
    } as ReturnType<typeof useApi>);

    render(<PnLChart />);
    await user.click(screen.getByText('Retry'));

    expect(mutate).toHaveBeenCalledTimes(1);
  });

  it('renders legend with total, realized, unrealized entries', () => {
    mockUseApi.mockReturnValue({
      data: { total_pnl: 0, realized_pnl: 0, unrealized_pnl: 0 },
      error: undefined,
      isLoading: false,
      mutate: jest.fn(),
    } as ReturnType<typeof useApi>);

    render(<PnLChart />);

    expect(screen.getByText('total')).toBeInTheDocument();
    expect(screen.getByText('realized')).toBeInTheDocument();
    expect(screen.getByText('unrealized')).toBeInTheDocument();
  });
});
