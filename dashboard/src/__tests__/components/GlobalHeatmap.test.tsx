import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// Mock heavy deps before importing the component
jest.mock('@/lib/websocket', () => ({
  getFeedManager: jest.fn(() => ({})),
}));

jest.mock('@/hooks/useWebSocket', () => ({
  useWebSocket: jest.fn(() => ({ lastMessage: null, connected: false })),
}));

jest.mock('@/hooks/useApi', () => ({
  useApi: jest.fn(() => ({ data: undefined, isLoading: false, isValidating: false, error: undefined, mutate: jest.fn() })),
}));

jest.mock('@/lib/api', () => ({
  getExchangeStatus: jest.fn(),
}));

import { GlobalHeatmap } from '@/components/GlobalHeatmap';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useApi } from '@/hooks/useApi';

const mockUseWebSocket = useWebSocket as jest.MockedFunction<typeof useWebSocket>;
const mockUseApi = useApi as jest.MockedFunction<typeof useApi>;

// Stable grid — silence Math.random variance in tests
beforeAll(() => {
  jest.spyOn(Math, 'random').mockReturnValue(0.5);
});

afterAll(() => {
  (Math.random as jest.MockedFunction<typeof Math.random>).mockRestore();
});

beforeEach(() => {
  mockUseWebSocket.mockReturnValue({ lastMessage: null, connected: false } as ReturnType<typeof useWebSocket>);
  mockUseApi.mockReturnValue({ data: undefined, isLoading: false, isValidating: false, error: undefined, mutate: jest.fn() } as ReturnType<typeof useApi>);
  localStorage.clear();
  jest.useFakeTimers();
});

afterEach(() => {
  jest.clearAllTimers();
  jest.useRealTimers();
});

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

describe('GlobalHeatmap — rendering', () => {
  it('renders the Spread Heatmap label', () => {
    render(<GlobalHeatmap />);
    expect(screen.getByText(/spread heatmap/i)).toBeInTheDocument();
  });

  it('renders all four symbol-set buttons', () => {
    render(<GlobalHeatmap />);
    expect(screen.getByRole('button', { name: /major 8/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /top 20/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /all/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /custom/i })).toBeInTheDocument();
  });

  it('shows OFFLINE status when WebSocket is not connected', () => {
    render(<GlobalHeatmap />);
    expect(screen.getByText(/offline/i)).toBeInTheDocument();
  });

  it('shows LIVE status when WebSocket is connected', () => {
    mockUseWebSocket.mockReturnValue({ lastMessage: null, connected: true } as ReturnType<typeof useWebSocket>);
    render(<GlobalHeatmap />);
    expect(screen.getByText(/live/i)).toBeInTheDocument();
  });

  it('renders the heatmap table', () => {
    render(<GlobalHeatmap />);
    expect(screen.getByRole('table')).toBeInTheDocument();
  });

  it('renders EX \\ SYM header cell', () => {
    render(<GlobalHeatmap />);
    expect(screen.getByText(/EX.*SYM/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Default symbol set: Major 8
// ---------------------------------------------------------------------------

describe('GlobalHeatmap — default Major 8 symbol set', () => {
  it('shows BTC and ETH column headers by default', () => {
    render(<GlobalHeatmap />);
    expect(screen.getByRole('columnheader', { name: 'BTC' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'ETH' })).toBeInTheDocument();
  });

  it('shows all 8 major symbols as column headers', () => {
    render(<GlobalHeatmap />);
    const majorSymbols = ['BTC', 'ETH', 'XRP', 'SOL', 'BNB', 'DOGE', 'ADA', 'AVAX'];
    for (const sym of majorSymbols) {
      expect(screen.getByRole('columnheader', { name: sym })).toBeInTheDocument();
    }
  });

  it('does not show Top 20 only symbols by default', () => {
    render(<GlobalHeatmap />);
    // DOT is in Top 20 but not Major 8
    expect(screen.queryByRole('columnheader', { name: 'DOT' })).not.toBeInTheDocument();
  });

  it('renders fallback exchange rows', () => {
    render(<GlobalHeatmap />);
    expect(screen.getByText(/binance/i)).toBeInTheDocument();
    expect(screen.getByText(/bybit/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// US-110: Symbol set switching
// ---------------------------------------------------------------------------

describe('GlobalHeatmap — US-110 symbol set switching', () => {
  it('switches to Top 20 and shows DOT column', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<GlobalHeatmap />);

    await user.click(screen.getByRole('button', { name: /top 20/i }));

    expect(screen.getByRole('columnheader', { name: 'DOT' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'LINK' })).toBeInTheDocument();
  });

  it('switching to Top 20 still includes Major 8 symbols', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<GlobalHeatmap />);

    await user.click(screen.getByRole('button', { name: /top 20/i }));

    expect(screen.getByRole('columnheader', { name: 'BTC' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'ETH' })).toBeInTheDocument();
  });

  it('clicking Custom button opens the custom symbol input box', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<GlobalHeatmap />);

    await user.click(screen.getByRole('button', { name: /custom/i }));

    expect(screen.getByPlaceholderText(/btc,eth,sol/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /apply/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
  });

  it('clicking Custom twice toggles the input box off', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<GlobalHeatmap />);

    await user.click(screen.getByRole('button', { name: /custom/i }));
    expect(screen.getByPlaceholderText(/btc,eth,sol/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /custom/i }));
    expect(screen.queryByPlaceholderText(/btc,eth,sol/i)).not.toBeInTheDocument();
  });

  it('Apply saves custom symbols to localStorage and displays them', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<GlobalHeatmap />);

    await user.click(screen.getByRole('button', { name: /custom/i }));

    const input = screen.getByPlaceholderText(/btc,eth,sol/i);
    await user.clear(input);
    await user.type(input, 'AAPL,TSLA,GOOG');
    await user.click(screen.getByRole('button', { name: /apply/i }));

    // Custom box closes
    expect(screen.queryByPlaceholderText(/btc,eth,sol/i)).not.toBeInTheDocument();

    // Custom symbols appear as column headers
    expect(screen.getByRole('columnheader', { name: 'AAPL' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'TSLA' })).toBeInTheDocument();

    // Persisted to localStorage
    expect(localStorage.getItem('leviathan_heatmap_custom')).toBe('AAPL,TSLA,GOOG');
  });

  it('Enter key in custom input triggers Apply', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<GlobalHeatmap />);

    await user.click(screen.getByRole('button', { name: /custom/i }));

    const input = screen.getByPlaceholderText(/btc,eth,sol/i);
    await user.clear(input);
    await user.type(input, 'DOGE,SHIB{Enter}');

    // Box closes and symbols applied
    expect(screen.queryByPlaceholderText(/btc,eth,sol/i)).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'DOGE' })).toBeInTheDocument();
  });

  it('Cancel closes custom box without applying symbols', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<GlobalHeatmap />);

    // Start on Major 8
    expect(screen.getByRole('columnheader', { name: 'BTC' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /custom/i }));
    const input = screen.getByPlaceholderText(/btc,eth,sol/i);
    await user.clear(input);
    await user.type(input, 'CUSTOM1,CUSTOM2');
    await user.click(screen.getByRole('button', { name: /cancel/i }));

    // Box closed
    expect(screen.queryByPlaceholderText(/btc,eth,sol/i)).not.toBeInTheDocument();

    // Still showing BTC (Major 8 unchanged)
    expect(screen.getByRole('columnheader', { name: 'BTC' })).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'CUSTOM1' })).not.toBeInTheDocument();
  });

  it('loads custom symbols from localStorage on mount', () => {
    localStorage.setItem('leviathan_heatmap_custom', 'STORED1,STORED2');
    render(<GlobalHeatmap />);

    // Open custom box — input should pre-fill from localStorage
    fireEvent.click(screen.getByRole('button', { name: /custom/i }));

    const input = screen.getByPlaceholderText(/btc,eth,sol/i) as HTMLInputElement;
    expect(input.value).toBe('STORED1,STORED2');
  });

  it('custom input uppercases and trims symbols on Apply', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<GlobalHeatmap />);

    await user.click(screen.getByRole('button', { name: /custom/i }));
    const input = screen.getByPlaceholderText(/btc,eth,sol/i);
    await user.clear(input);
    await user.type(input, ' btc , eth ');
    await user.click(screen.getByRole('button', { name: /apply/i }));

    expect(screen.getByRole('columnheader', { name: 'BTC' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'ETH' })).toBeInTheDocument();
  });

  it('empty custom input falls back to Major 8 symbols', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<GlobalHeatmap />);

    await user.click(screen.getByRole('button', { name: /custom/i }));
    const input = screen.getByPlaceholderText(/btc,eth,sol/i);
    await user.clear(input);
    await user.click(screen.getByRole('button', { name: /apply/i }));

    // Falls back to MAJOR_8
    expect(screen.getByRole('columnheader', { name: 'BTC' })).toBeInTheDocument();
  });

  it('switching from Top 20 back to Major 8 removes extra symbols', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<GlobalHeatmap />);

    await user.click(screen.getByRole('button', { name: /top 20/i }));
    expect(screen.getByRole('columnheader', { name: 'DOT' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /major 8/i }));
    expect(screen.queryByRole('columnheader', { name: 'DOT' })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Exchange status integration
// ---------------------------------------------------------------------------

describe('GlobalHeatmap — exchange status', () => {
  it('shows STATIC when no exchange status from API', () => {
    render(<GlobalHeatmap />);
    expect(screen.getByText(/static/i)).toBeInTheDocument();
  });

  it('shows API status and exchange rows from API data', () => {
    mockUseApi.mockReturnValue({
      data: {
        binance: { connected: true },
        bybit:   { connected: false },
      },
      isLoading: false,
      isValidating: false,
      error: undefined,
      mutate: jest.fn(),
    } as ReturnType<typeof useApi>);

    render(<GlobalHeatmap />);
    expect(screen.getByText(/api/i)).toBeInTheDocument();
    // API-derived exchange names appear
    expect(screen.getByText(/binance/i)).toBeInTheDocument();
    expect(screen.getByText(/bybit/i)).toBeInTheDocument();
  });
});
