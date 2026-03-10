"use client";

import { useState } from "react";
import Link from "next/link";
import { Memory } from "@/hooks/useMemories";
import { TrailView } from "./TrailView";
import { formatRelativeTime } from "@/lib/format";
import {
  ArrowLeft,
  Pencil,
  Trash2,
  Save,
  X,
  MessageSquare,
  Sparkles,
  Pencil as PencilIcon,
  Wrench,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
} from "lucide-react";

interface MemoryDetailProps {
  memory: Memory;
  onBack: () => void;
  onCorrect: (id: string, content: string, category?: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

const sourceIcons = {
  extracted: Sparkles,
  manual: PencilIcon,
  tool: Wrench,
} as const;

const statusConfig = {
  active: {
    label: "Active",
    icon: CheckCircle2,
    color: "text-status-success",
    bgColor: "bg-status-success-bg",
    borderColor: "border-status-success/30",
  },
  superseded: {
    label: "Superseded",
    icon: Clock,
    color: "text-text-muted",
    bgColor: "bg-bg-tertiary",
    borderColor: "border-border-primary",
  },
  rejected: {
    label: "Rejected",
    icon: XCircle,
    color: "text-status-error",
    bgColor: "bg-status-error-bg",
    borderColor: "border-status-error/30",
  },
  deleted: {
    label: "Deleted",
    icon: Trash2,
    color: "text-status-error",
    bgColor: "bg-status-error-bg",
    borderColor: "border-status-error/30",
  },
} as const;

function getSourceIcon(sourceType: string) {
  return sourceIcons[sourceType as keyof typeof sourceIcons] || Sparkles;
}

function getStatusConfig(status: string) {
  return (
    statusConfig[status as keyof typeof statusConfig] || {
      label: status,
      icon: CheckCircle2,
      color: "text-text-primary",
      bgColor: "bg-bg-tertiary",
      borderColor: "border-border-primary",
    }
  );
}

export function MemoryDetail({
  memory,
  onBack,
  onCorrect,
  onDelete,
}: MemoryDetailProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editedContent, setEditedContent] = useState(memory.content);
  const [editedCategory, setEditedCategory] = useState(memory.category);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const SourceIcon = getSourceIcon(memory.source_type);
  const statusConfig = getStatusConfig(memory.status);
  const StatusIcon = statusConfig.icon;
  const confidence = memory.metadata?.confidence as number | undefined;

  const handleSave = async () => {
    if (!editedContent.trim()) return;
    setIsSaving(true);
    try {
      await onCorrect(memory.id, editedContent.trim(), editedCategory.trim());
      setIsEditing(false);
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    setEditedContent(memory.content);
    setEditedCategory(memory.category);
    setIsEditing(false);
  };

  const handleDeleteConfirm = async () => {
    setIsDeleting(true);
    try {
      await onDelete(memory.id);
    } finally {
      setIsDeleting(false);
      setShowDeleteDialog(false);
    }
  };

  return (
    <div className="animate-fade-in space-y-6">
      {/* Back button */}
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-2 px-3 py-2 text-sm text-text-secondary hover:text-text-primary hover:bg-bg-tertiary rounded-md transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to memories
        </button>
      </div>

      {/* Memory Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <div
            className={`w-10 h-10 rounded-lg ${statusConfig.bgColor} flex items-center justify-center`}
          >
            <StatusIcon className={`w-5 h-5 ${statusConfig.color}`} />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-text-primary">
              Memory Details
            </h2>
            <p className="text-sm text-text-muted">
              {formatRelativeTime(memory.created_at)}
            </p>
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          {!isEditing ? (
            <>
              <button
                type="button"
                onClick={() => setIsEditing(true)}
                className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium text-text-primary bg-bg-tertiary hover:bg-bg-tertiary/80 rounded-md transition-colors"
              >
                <Pencil className="w-4 h-4" />
                Edit
              </button>
              <button
                type="button"
                onClick={() => setShowDeleteDialog(true)}
                className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium text-status-error bg-status-error-bg hover:bg-status-error hover:text-white rounded-md transition-colors"
              >
                <Trash2 className="w-4 h-4" />
                Delete
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={handleCancel}
                disabled={isSaving}
                className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium text-text-secondary hover:text-text-primary hover:bg-bg-tertiary rounded-md transition-colors disabled:opacity-50"
              >
                <X className="w-4 h-4" />
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={isSaving || !editedContent.trim()}
                className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium text-white bg-accent-primary hover:bg-accent-primary/90 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSaving ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Saving...
                  </>
                ) : (
                  <>
                    <Save className="w-4 h-4" />
                    Save
                  </>
                )}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-text-secondary mb-2">
            Content
          </label>
          {isEditing ? (
            <textarea
              value={editedContent}
              onChange={(e) => setEditedContent(e.target.value)}
              className="w-full min-h-[120px] px-3 py-2 text-sm text-text-primary bg-bg-primary border border-border-primary rounded-md resize-y focus:outline-none focus:ring-2 focus:ring-border-focus/50"
              placeholder="Enter memory content..."
            />
          ) : (
            <div className="p-4 bg-bg-secondary border border-border-primary rounded-md">
              <p className="text-sm text-text-primary whitespace-pre-wrap">
                {memory.content}
              </p>
            </div>
          )}
        </div>

        {/* Metadata Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {/* Category */}
          <div>
            <label className="block text-xs font-medium text-text-muted mb-1">
              Category
            </label>
            {isEditing ? (
              <input
                type="text"
                value={editedCategory}
                onChange={(e) => setEditedCategory(e.target.value)}
                className="w-full px-3 py-2 text-sm text-text-primary bg-bg-primary border border-border-primary rounded-md focus:outline-none focus:ring-2 focus:ring-border-focus/50"
              />
            ) : (
              <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-bg-tertiary text-text-primary">
                {memory.category}
              </span>
            )}
          </div>

          {/* Source Type */}
          <div>
            <label className="block text-xs font-medium text-text-muted mb-1">
              Source
            </label>
            <div className="flex items-center gap-2">
              <SourceIcon className="w-4 h-4 text-text-muted" />
              <span className="text-sm text-text-primary capitalize">
                {memory.source_type}
              </span>
            </div>
          </div>

          {/* Status */}
          <div>
            <label className="block text-xs font-medium text-text-muted mb-1">
              Status
            </label>
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${statusConfig.bgColor} ${statusConfig.color}`}
            >
              <StatusIcon className="w-3 h-3" />
              {statusConfig.label}
            </span>
          </div>

          {/* Confidence */}
          {confidence !== undefined && (
            <div>
              <label className="block text-xs font-medium text-text-muted mb-1">
                Confidence
              </label>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      confidence >= 0.8
                        ? "bg-status-success"
                        : confidence >= 0.5
                          ? "bg-status-warning"
                          : "bg-status-error"
                    }`}
                    style={{ width: `${confidence * 100}%` }}
                  />
                </div>
                <span className="text-xs text-text-secondary">
                  {(confidence * 100).toFixed(0)}%
                </span>
              </div>
            </div>
          )}

          {/* Memory Slot */}
          {(memory.metadata?.memory_slot as number | undefined) !== undefined && (
            <div>
              <label className="block text-xs font-medium text-text-muted mb-1">
                Memory Slot
              </label>
              <span className="text-sm text-text-primary">
                {String(memory.metadata?.memory_slot as number)}
              </span>
            </div>
          )}

          {/* Valid From */}
          {memory.metadata && (memory.metadata.valid_from as string | undefined) && (
            <div>
              <label className="block text-xs font-medium text-text-muted mb-1">
                Valid From
              </label>
              <span className="text-sm text-text-primary">
                {formatRelativeTime(memory.metadata?.valid_from as string)}
              </span>
            </div>
          )}

          {/* Valid To */}
          {memory.metadata && (memory.metadata.valid_to as string | undefined) && (
            <div>
              <label className="block text-xs font-medium text-text-muted mb-1">
                Valid To
              </label>
              <span className="text-sm text-text-primary">
                {formatRelativeTime(memory.metadata?.valid_to as string)}
              </span>
            </div>
          )}

          {/* Created */}
          <div>
            <label className="block text-xs font-medium text-text-muted mb-1">
              Created
            </label>
            <span className="text-sm text-text-primary">
              {formatRelativeTime(memory.created_at)}
            </span>
          </div>

          {/* Updated */}
          <div>
            <label className="block text-xs font-medium text-text-muted mb-1">
              Updated
            </label>
            <span className="text-sm text-text-primary">
              {formatRelativeTime(memory.updated_at)}
            </span>
          </div>

          {/* Confirmed */}
          <div>
            <label className="block text-xs font-medium text-text-muted mb-1">
              Confirmed
            </label>
            <span className="text-sm text-text-primary">
              {memory.confirmed ? "Yes" : "No"}
            </span>
          </div>
        </div>

        {/* Source Conversation Link */}
        {memory.conversation_id && (
          <div>
            <label className="block text-xs font-medium text-text-muted mb-1">
              Source Conversation
            </label>
            <Link
              href={`/?id=${memory.conversation_id}`}
              className="inline-flex items-center gap-2 text-sm text-accent-primary hover:text-accent-primary/80 transition-colors"
            >
              <MessageSquare className="w-4 h-4" />
              View conversation
            </Link>
          </div>
        )}
      </div>

      {/* Trail View */}
      <div className="pt-6 border-t border-border-primary">
        <TrailView memoryId={memory.id} />
      </div>

      {/* Delete Confirmation Dialog */}
      {showDeleteDialog && (
        <div className="fixed inset-0 z-modal flex items-center justify-center p-4">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-bg-overlay"
            onClick={() => setShowDeleteDialog(false)}
          />

          {/* Dialog */}
          <div className="relative w-full max-w-md bg-bg-secondary rounded-xl border border-border-primary shadow-xl animate-scale">
            <div className="p-6">
              {/* Dialog Header */}
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-status-error-bg flex items-center justify-center">
                  <AlertTriangle className="w-5 h-5 text-status-error" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-text-primary">
                    Delete Memory?
                  </h3>
                </div>
              </div>

              {/* Dialog Body */}
              <p className="text-sm text-text-secondary mb-6">
                This will permanently delete this memory. This action cannot be
                undone and the memory will be removed from the system.
              </p>

              {/* Dialog Actions */}
              <div className="flex items-center justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setShowDeleteDialog(false)}
                  className="px-4 py-2 text-sm font-medium text-text-secondary hover:text-text-primary hover:bg-bg-tertiary rounded-md transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleDeleteConfirm}
                  disabled={isDeleting}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-status-error text-white font-medium rounded-md hover:bg-status-error/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isDeleting ? (
                    <>
                      <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Deleting...
                    </>
                  ) : (
                    <>
                      <Trash2 className="w-4 h-4" />
                      Delete
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
