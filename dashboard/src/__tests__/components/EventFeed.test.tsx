import React from 'react';
import { render, screen } from '@testing-library/react';

jest.mock('@/hooks/useApi', () => ({
  useApi: jest.fn(),
}));

import { useApi } from '@/hooks/useApi';
import { EventFeed } from '@/components/EventFeed';

const mockUseApi = useApi as jest.MockedFunction<typeof useApi>;

const mockAlerts = [
  { id: '1', type: 'kill_switch', severity: 'critical' as const, message: 'Kill switch activated', timestamp: '2026-03-11T01:00:00Z' },
  { id: '2', type: 'drawdown',    severity: 'warning'  as const, message: 'Drawdown exceeds 5%',   timestamp: '2026-03-11T00:55:00Z' },
  { id: '3', type: 'info',        severity: 'info'     as const, message: 'Strategy enabled',       timestamp: '2026-03-11T00:50:00Z' },
];

beforeEach(() => mockUseApi.mockClear());

describe('EventFeed', () => {
  describe('alert list rendering', () => {
    it('renders alert messages when data is available', () => {
      mockUseApi.mockReturnValue({ data: mockAlerts, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<EventFeed />);

      expect(screen.getByText('Kill switch activated')).toBeInTheDocument();
      expect(screen.getByText('Drawdown exceeds 5%')).toBeInTheDocument();
      expect(screen.getByText('Strategy enabled')).toBeInTheDocument();
    });

    it('renders all three alert messages in the list', () => {
      mockUseApi.mockReturnValue({ data: mockAlerts, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<EventFeed />);

      // Each alert message appears once in the DOM
      expect(screen.getByText('Kill switch activated')).toBeInTheDocument();
      expect(screen.getByText('Drawdown exceeds 5%')).toBeInTheDocument();
      expect(screen.getByText('Strategy enabled')).toBeInTheDocument();
    });
  });

  describe('severity badges', () => {
    it('renders a badge for each alert severity level', () => {
      mockUseApi.mockReturnValue({ data: mockAlerts, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<EventFeed />);

      expect(screen.getByText('critical')).toBeInTheDocument();
      expect(screen.getByText('warning')).toBeInTheDocument();
      expect(screen.getByText('info')).toBeInTheDocument();
    });
  });

  describe('empty state', () => {
    it('shows "No recent events" when alert list is empty', () => {
      mockUseApi.mockReturnValue({ data: [], error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<EventFeed />);

      expect(screen.getByText(/no recent events/i)).toBeInTheDocument();
    });

    it('shows "No recent events" when data is null', () => {
      mockUseApi.mockReturnValue({ data: undefined, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<EventFeed />);

      expect(screen.getByText(/no recent events/i)).toBeInTheDocument();
    });
  });

  describe('navigation', () => {
    it('renders a "View all" link pointing to /alerts', () => {
      mockUseApi.mockReturnValue({ data: mockAlerts, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<EventFeed />);

      const link = screen.getByRole('link', { name: /view all/i });
      expect(link).toBeInTheDocument();
      expect(link).toHaveAttribute('href', '/alerts');
    });
  });

  describe('timestamp display', () => {
    it('renders timestamps for each alert row', () => {
      mockUseApi.mockReturnValue({ data: mockAlerts, error: undefined, isLoading: false, mutate: jest.fn() } as ReturnType<typeof useApi>);

      render(<EventFeed />);

      // Timestamps should be formatted and visible
      const timestamps = screen.getAllByText(/\d{2}:\d{2}/);
      expect(timestamps.length).toBeGreaterThanOrEqual(1);
    });
  });
});
