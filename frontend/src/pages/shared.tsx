import type { ReactNode } from "react";
import { AlertCircle, ArrowUpRight, RefreshCw } from "lucide-react";
import {
  Button,
  Card,
  Input,
  Modal,
  Skeleton,
  Textarea,
} from "../components/ui";
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions}
    </header>
  );
}
export function QueryState({
  error,
  loading,
  retry,
}: {
  error: Error | null;
  loading: boolean;
  retry: () => unknown;
}) {
  if (error)
    return (
      <div className="page-error">
        <AlertCircle size={22} />
        <h2>This view needs attention</h2>
        <p>{error.message}</p>
        <Button variant="secondary" onClick={() => retry()}>
          <RefreshCw size={14} />
          Try again
        </Button>
      </div>
    );
  return loading ? (
    <div className="page-grid">
      <Skeleton className="h-64" />
      <Skeleton className="h-64" />
    </div>
  ) : null;
}
export function Evidence({ data }: { data: unknown }) {
  return (
    <details className="details">
      <summary>Evidence and details</summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  );
}
export interface FormField {
  name: string;
  label: string;
  type?: string;
  value?: string | number;
  required?: boolean;
  hint?: string;
  min?: number;
  max?: number;
}
export function FormDialog({
  open,
  onOpenChange,
  title,
  description,
  fields,
  submitLabel = "Save",
  onSubmit,
  pending = false,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  fields: FormField[];
  submitLabel?: string;
  onSubmit: (
    data: Record<string, string>,
    form: HTMLFormElement,
  ) => Promise<unknown>;
  pending?: boolean;
  children?: ReactNode;
}) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={description}
    >
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          const form = event.currentTarget;
          onSubmit(
            Object.fromEntries(new FormData(form)) as Record<string, string>,
            form,
          );
        }}
      >
        {fields.map((field) => (
          <label className="field" key={field.name}>
            {field.label}
            {field.type === "textarea" ? (
              <Textarea
                aria-label={field.label}
                name={field.name}
                defaultValue={field.value}
                required={field.required}
              />
            ) : (
              <Input
                aria-label={field.label}
                name={field.name}
                type={field.type || "text"}
                defaultValue={field.value}
                required={field.required}
                min={field.min}
                max={field.max}
                autoComplete={
                  field.type === "password" ? "new-password" : undefined
                }
              />
            )}{" "}
            {field.hint && <span className="field-hint">{field.hint}</span>}
          </label>
        ))}
        {children}
        <div className="form-actions">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" loading={pending}>
            {submitLabel}
            <ArrowUpRight size={14} />
          </Button>
        </div>
      </form>
    </Modal>
  );
}
export function Metric({
  label,
  value,
  footnote,
}: {
  label: string;
  value: ReactNode;
  footnote?: string;
}) {
  return (
    <Card>
      <div className="metric-label">{label}</div>
      <div className="metric">{value}</div>
      {footnote && <div className="field-hint">{footnote}</div>}
    </Card>
  );
}
