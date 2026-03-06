import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { KillSwitch } from '@/components/KillSwitch';

jest.mock('@/lib/api', () => ({
  killEngine: jest.fn(),
}));

import { killEngine } from '@/lib/api';
const mockKillEngine = killEngine as jest.MockedFunction<typeof killEngine>;

beforeEach(() => mockKillEngine.mockClear());

describe('KillSwitch', () => {
  it('renders the kill switch button', () => {
    render(<KillSwitch />);
    expect(
      screen.getByRole('button', { name: /activate emergency stop/i }),
    ).toBeInTheDocument();
    expect(screen.getByText('KILL SWITCH')).toBeInTheDocument();
  });

  it('opens confirmation dialog on first click', async () => {
    const user = userEvent.setup();
    render(<KillSwitch />);

    await user.click(screen.getByRole('button', { name: /activate emergency stop/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/EMERGENCY STOP/i)).toBeInTheDocument();
    expect(screen.getByText('HALT ALL TRADING')).toBeInTheDocument();
  });

  it('calls POST /kill on confirm and shows HALTED state', async () => {
    const user = userEvent.setup();
    const onKill = jest.fn();
    mockKillEngine.mockResolvedValueOnce({ status: 'halted', reason: 'manual kill switch' });

    render(<KillSwitch onKill={onKill} />);

    await user.click(screen.getByRole('button', { name: /activate emergency stop/i }));
    await user.click(screen.getByText('HALT ALL TRADING'));

    await waitFor(() => {
      expect(mockKillEngine).toHaveBeenCalledWith('manual kill switch');
      expect(onKill).toHaveBeenCalledTimes(1);
    });

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByText('HALTED')).toBeInTheDocument();
  });

  it('closes dialog on cancel without calling killEngine', async () => {
    const user = userEvent.setup();
    render(<KillSwitch />);

    await user.click(screen.getByRole('button', { name: /activate emergency stop/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await user.click(screen.getByText('Cancel'));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mockKillEngine).not.toHaveBeenCalled();
  });

  it('shows SYSTEM HALTED banner when isActive=true', () => {
    render(<KillSwitch isActive={true} />);

    const alert = screen.getByRole('alert');
    expect(alert).toBeInTheDocument();
    expect(alert).toHaveTextContent(/SYSTEM HALTED/i);
    expect(screen.getByText('HALTED')).toBeInTheDocument();
  });

  it('button is disabled when isActive=true', () => {
    render(<KillSwitch isActive={true} />);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('does not open dialog when already active', async () => {
    const user = userEvent.setup();
    render(<KillSwitch isActive={true} />);

    await user.click(screen.getByRole('button'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
