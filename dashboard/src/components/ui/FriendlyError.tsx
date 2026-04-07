import React from "react";
import { AlertCircle, RefreshCw } from "lucide-react";
import ko from "@/i18n/ko.json";

const ERROR_MESSAGES: Record<string, string> = {
  "Failed to fetch":             ko.error.networkError,
  "Network Error":               ko.error.networkError,
  "NetworkError":                ko.error.networkError,
  "net::ERR":                    ko.error.networkError,
  "Unauthorized":                ko.error.authError,
  "401":                         ko.error.authError,
  "403":                         ko.error.authError,
  "500":                         ko.error.serverError,
  "Internal Server Error":       ko.error.serverError,
  "404":                         ko.error.notFound,
  "Not Found":                   ko.error.notFound,
  "TimeoutError":                ko.error.timeout,
  "AbortError":                  ko.error.timeout,
};

function humanize(error: string | Error | unknown): string {
  const msg = error instanceof Error ? error.message : String(error ?? "");
  for (const [key, value] of Object.entries(ERROR_MESSAGES)) {
    if (msg.includes(key)) return value;
  }
  return ko.error.unknown;
}

interface FriendlyErrorProps {
  error: string | Error | unknown;
  onRetry?: () => void;
  compact?: boolean;
  className?: string;
}

export function FriendlyError({ error, onRetry, compact = false, className = "" }: FriendlyErrorProps) {
  const message = humanize(error);

  if (compact) {
    return (
      <div className={`flex items-center gap-2 text-danger text-caption ${className}`}>
        <AlertCircle size={14} aria-hidden />
        <span>{message}</span>
        {onRetry && (
          <button onClick={onRetry} className="underline underline-offset-2 hover:no-underline">
            {ko.error.retry}
          </button>
        )}
      </div>
    );
  }

  return (
    <div
      className={`flex flex-col items-center justify-center py-12 px-6 text-center ${className}`}
      role="alert"
    >
      <div className="w-14 h-14 rounded-full bg-danger-bg flex items-center justify-center mb-4">
        <AlertCircle size={24} className="text-danger" aria-hidden />
      </div>
      <p className="text-body font-medium text-text-primary mb-1">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-4 btn-outlined text-small flex items-center gap-2"
        >
          <RefreshCw size={14} aria-hidden />
          {ko.error.retry}
        </button>
      )}
    </div>
  );
}
