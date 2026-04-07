import React from "react";
import { LucideIcon, Inbox } from "lucide-react";
import ko from "@/i18n/ko.json";

interface EmptyStateProps {
  icon?: LucideIcon;
  title?: string;
  description?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
  className?: string;
}

export function EmptyState({
  icon: Icon = Inbox,
  title = ko.empty.noData,
  description = ko.empty.noDataDesc,
  action,
  className = "",
}: EmptyStateProps) {
  return (
    <div
      className={`flex flex-col items-center justify-center py-16 px-6 text-center ${className}`}
      role="status"
      aria-label={title}
    >
      <div className="w-16 h-16 rounded-full bg-bg-surface flex items-center justify-center mb-4">
        <Icon size={28} className="text-text-tertiary" aria-hidden />
      </div>
      <h3 className="text-body font-semibold text-text-primary mb-1">{title}</h3>
      {description && (
        <p className="text-caption text-text-secondary max-w-xs">{description}</p>
      )}
      {action && (
        <button
          onClick={action.onClick}
          className="mt-4 btn-subtle text-small px-4 py-2"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
