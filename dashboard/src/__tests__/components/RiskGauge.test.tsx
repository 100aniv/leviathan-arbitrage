import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

jest.mock('@/hooks/useApi', () => ({
  useApi: jest.fn(),
}));

import { useApi } from '@/hooks/useApi';
import { RiskGauge } from '@/components/RiskGauge';

const mockUseApi = useApi as jest.MockedFunction<typeof useApi>;

function makeRiskData(overrides = {}) {
  return {
    kill_switch_active: false,
    circuit_breaker_state: 'CLOSED',
    max_drawdown_pct: 3.5,
    daily_loss_pct: 1.2,
    position_count: 2,
    correlation_alert: false,
    ...overrides,
  };
}

beforeEach(() => mockUseApi.mockClear());

describe('RiskGauge', () => {
  describe('gauge rendering', () => {
    it('renders the risk gauge heading', () => {
      mockUseApi.mockReturnValue({ data: makeRiskData(), error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<RiskGauge />);

      expect(screen.getAllByText(/drawdown|risk/i).length).toBeGreaterThanOrEqual(1);
    });

    it('renders an SVG element for the semicircle arc', () => {
      mockUseApi.mockReturnValue({ data: makeRiskData({ max_drawdown_pct: 8.0 }), error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      const { container } = render(<RiskGauge />);

      expect(container.querySelector('svg')).toBeInTheDocument();
    });

    it('displays the drawdown percentage value', () => {
      mockUseApi.mockReturnValue({ data: makeRiskData({ max_drawdown_pct: 12.5 }), error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<RiskGauge />);

      expect(screen.getByText(/12\.5|12,5/)).toBeInTheDocument();
    });
  });

  describe('kill switch badge', () => {
    it('shows STANDBY badge when kill switch is inactive', () => {
      mockUseApi.mockReturnValue({ data: makeRiskData({ kill_switch_active: false }), error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<RiskGauge />);

      expect(screen.getByText('STANDBY')).toBeInTheDocument();
    });

    it('shows ACTIVE badge when kill switch is triggered', () => {
      mockUseApi.mockReturnValue({ data: makeRiskData({ kill_switch_active: true }), error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<RiskGauge />);

      expect(screen.getByText('ACTIVE')).toBeInTheDocument();
    });
  });

  describe('circuit breaker badge', () => {
    it('shows CLOSED badge for normal circuit breaker state', () => {
      mockUseApi.mockReturnValue({ data: makeRiskData({ circuit_breaker_state: 'CLOSED' }), error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<RiskGauge />);

      expect(screen.getByText('CLOSED')).toBeInTheDocument();
    });

    it('shows OPEN badge when circuit breaker is tripped', () => {
      mockUseApi.mockReturnValue({ data: makeRiskData({ circuit_breaker_state: 'OPEN' }), error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<RiskGauge />);

      expect(screen.getByText('OPEN')).toBeInTheDocument();
    });

    it('shows HALF_OPEN badge during recovery state', () => {
      mockUseApi.mockReturnValue({ data: makeRiskData({ circuit_breaker_state: 'HALF_OPEN' }), error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<RiskGauge />);

      expect(screen.getByText('HALF_OPEN')).toBeInTheDocument();
    });
  });

  describe('navigation', () => {
    it('renders a "View all" link pointing to /risk', () => {
      mockUseApi.mockReturnValue({ data: makeRiskData(), error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<RiskGauge />);

      const link = screen.getByRole('link', { name: /view all/i });
      expect(link).toBeInTheDocument();
      expect(link).toHaveAttribute('href', '/risk');
    });
  });

  describe('error state', () => {
    it('shows retry button when API call fails', () => {
      mockUseApi.mockReturnValue({ data: undefined, error: new Error('Network error'), isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<RiskGauge />);

      expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
    });

    it('calls mutate when retry button is clicked', async () => {
      const user = userEvent.setup();
      const mutate = jest.fn();
      mockUseApi.mockReturnValue({ data: undefined, error: new Error('fail'), isLoading: false, mutate } as ReturnType<typeof useApi>);

      render(<RiskGauge />);
      await user.click(screen.getByRole('button', { name: /retry/i }));

      expect(mutate).toHaveBeenCalledTimes(1);
    });
  });
});
