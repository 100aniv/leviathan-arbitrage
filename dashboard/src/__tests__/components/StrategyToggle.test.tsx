import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

jest.mock('@/lib/api', () => ({
  toggleStrategy: jest.fn(),
}));

import { toggleStrategy } from '@/lib/api';
import { StrategyToggle } from '@/components/StrategyToggle';

const mockToggle = toggleStrategy as jest.MockedFunction<typeof toggleStrategy>;

const baseStrategy = {
  id: 'tri-arb-1',
  type: 'triangular',
  name: 'Tri Arb',
  enabled: false,
  exchange_a: 'Binance',
  exchange_b: 'Upbit',
  symbol: 'BTC/USDT',
};

beforeEach(() => {
  jest.useFakeTimers();
  mockToggle.mockClear();
});

afterEach(() => {
  jest.clearAllTimers();
  jest.useRealTimers();
});

describe('StrategyToggle', () => {
  it('renders strategy name', () => {
    render(<StrategyToggle strategy={baseStrategy} />);
    expect(screen.getByText('Tri Arb')).toBeInTheDocument();
  });

  it('renders exchange/symbol metadata', () => {
    render(<StrategyToggle strategy={baseStrategy} />);
    expect(screen.getByText(/Binance/)).toBeInTheDocument();
    expect(screen.getByText(/BTC\/USDT/)).toBeInTheDocument();
  });

  it('renders toggle with correct initial aria-checked state', () => {
    render(<StrategyToggle strategy={baseStrategy} />);
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  });

  it('applies optimistic update on click and calls API', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    mockToggle.mockResolvedValueOnce({ id: 'tri-arb-1', enabled: true });
    const onChange = jest.fn();

    render(<StrategyToggle strategy={baseStrategy} onChange={onChange} />);
    const toggle = screen.getByRole('switch');

    await user.click(toggle);

    // Optimistic state shows enabled immediately
    expect(toggle).toHaveAttribute('aria-checked', 'true');

    await waitFor(() => {
      expect(mockToggle).toHaveBeenCalledWith('tri-arb-1');
      expect(onChange).toHaveBeenCalledWith('tri-arb-1', true);
    });
  });

  it('starts enabled and toggles to disabled', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    mockToggle.mockResolvedValueOnce({ id: 'tri-arb-1', enabled: false });

    render(<StrategyToggle strategy={{ ...baseStrategy, enabled: true }} />);
    const toggle = screen.getByRole('switch');
    expect(toggle).toHaveAttribute('aria-checked', 'true');

    await user.click(toggle);

    await waitFor(() => {
      expect(toggle).toHaveAttribute('aria-checked', 'false');
    });
  });

  it('rolls back optimistic update on API failure', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    mockToggle.mockRejectedValueOnce(new Error('Network error'));

    render(<StrategyToggle strategy={baseStrategy} />);
    const toggle = screen.getByRole('switch');

    await user.click(toggle);

    await waitFor(() => {
      // Rolled back to original state
      expect(toggle).toHaveAttribute('aria-checked', 'false');
    });
  });

  it('does not fire twice when toggling rapidly (pending guard)', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    // Never resolves during the rapid clicks
    mockToggle.mockReturnValue(new Promise(() => {}));

    render(<StrategyToggle strategy={baseStrategy} />);
    const toggle = screen.getByRole('switch');

    await user.click(toggle);  // first click: optimistic, isPending = true
    await user.click(toggle);  // second click: should be ignored

    expect(mockToggle).toHaveBeenCalledTimes(1);
  });
});
