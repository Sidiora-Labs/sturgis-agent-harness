import {
  forwardRef,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactElement,
  type ReactNode,
  type TextareaHTMLAttributes,
} from "react";
import {
  Dialog,
  DropdownMenu,
  Select as SelectPrimitive,
  Tabs as TabsPrimitive,
  Tooltip as TooltipPrimitive,
} from "radix-ui";
import { Check, ChevronDown, LoaderCircle, Sparkles, X } from "lucide-react";
import "./components.css";

export function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "icon";
  loading?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = "secondary",
      size = "md",
      loading = false,
      disabled,
      className,
      children,
      type = "button",
      ...props
    },
    ref,
  ) {
    return (
      <button
        {...props}
        ref={ref}
        type={type}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        className={cx(
          "st-button",
          `st-button--${variant}`,
          `st-button--${size}`,
          className,
        )}
      >
        {loading && (
          <LoaderCircle className="st-spin" size={16} aria-hidden="true" />
        )}
        {children}
      </button>
    );
  },
);

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement>
>(function Input({ className, ...props }, ref) {
  return <input {...props} ref={ref} className={cx("st-input", className)} />;
});

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...props }, ref) {
  return (
    <textarea
      {...props}
      ref={ref}
      className={cx("st-input", "st-textarea", className)}
    />
  );
});

export type CardProps = {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
};

export function Card({
  title,
  description,
  actions,
  children,
  className,
}: CardProps) {
  return (
    <section className={cx("st-card", className)}>
      {(title || description || actions) && (
        <header className="st-card__header">
          <div className="st-card__heading">
            {title && <h2 className="st-card__title">{title}</h2>}
            {description && (
              <p className="st-card__description">{description}</p>
            )}
          </div>
          {actions && <div className="st-card__actions">{actions}</div>}
        </header>
      )}
      <div className="st-card__content">{children}</div>
    </section>
  );
}

export type BadgeTone = "neutral" | "accent" | "success" | "warning" | "danger";
export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: BadgeTone;
  children: ReactNode;
}) {
  return (
    <span className={cx("st-badge", `st-badge--${tone}`)}>{children}</span>
  );
}

export function Skeleton({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      {...props}
      aria-hidden="true"
      className={cx("st-skeleton", className)}
    />
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="st-empty">
      <div className="st-empty__icon" aria-hidden="true">
        {icon || <Sparkles size={23} strokeWidth={1.6} />}
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action && <div className="st-empty__action">{action}</div>}
    </div>
  );
}

export function Tooltip({
  label,
  children,
}: {
  label: string;
  children: ReactElement;
}) {
  return (
    <TooltipPrimitive.Provider delayDuration={350} skipDelayDuration={150}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content sideOffset={7} className="st-tooltip">
            {label}
            <TooltipPrimitive.Arrow className="st-tooltip__arrow" />
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}

export function Dropdown({
  trigger,
  items,
}: {
  trigger: ReactElement;
  items: Array<{
    id: string;
    label: string;
    onSelect: () => void;
    destructive?: boolean;
    disabled?: boolean;
  }>;
}) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>{trigger}</DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={8}
          collisionPadding={12}
          className="st-menu"
        >
          {items.map((item) => (
            <DropdownMenu.Item
              key={item.id}
              className={cx(
                "st-menu-item",
                item.destructive && "st-menu-item--danger",
              )}
              onSelect={item.onSelect}
              disabled={item.disabled}
            >
              {item.label}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  const restoreFocus = useRef<HTMLElement | null>(null);
  const descriptionId = useId();
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="st-overlay" />
        <Dialog.Content
          className="st-modal"
          aria-describedby={description ? descriptionId : undefined}
          onOpenAutoFocus={() => {
            restoreFocus.current = document.activeElement as HTMLElement;
          }}
          onCloseAutoFocus={(event) => {
            if (restoreFocus.current?.isConnected) {
              event.preventDefault();
              restoreFocus.current.focus();
            }
          }}
        >
          <div className="st-modal__header">
            <Dialog.Title className="st-modal__title">{title}</Dialog.Title>
            <Dialog.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close dialog">
                <X size={18} />
              </Button>
            </Dialog.Close>
          </div>
          {description && (
            <Dialog.Description
              id={descriptionId}
              className="st-modal__description"
            >
              {description}
            </Dialog.Description>
          )}
          <div className="st-modal__body">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function Tabs({
  value,
  onValueChange,
  items,
  children,
}: {
  value: string;
  onValueChange: (value: string) => void;
  items: Array<{ value: string; label: string }>;
  children?: ReactNode;
}) {
  return (
    <TabsPrimitive.Root
      value={value}
      onValueChange={onValueChange}
      className="st-tabs"
    >
      <TabsPrimitive.List className="st-tabs__list" aria-label="View options">
        {items.map((item) => (
          <TabsPrimitive.Trigger
            key={item.value}
            value={item.value}
            className="st-tabs__trigger"
          >
            {item.label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {items.map((item) => (
        <TabsPrimitive.Content
          key={item.value}
          value={item.value}
          className="st-tabs__panel"
        >
          {item.value === value ? children : null}
        </TabsPrimitive.Content>
      ))}
    </TabsPrimitive.Root>
  );
}

export function Select({
  label,
  value,
  onValueChange,
  items,
  placeholder = "Choose an option",
  disabled = false,
}: {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  items: Array<{ value: string; label: string }>;
  placeholder?: string;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="st-select-field">
      <label className="st-label" htmlFor={id}>
        {label}
      </label>
      <SelectPrimitive.Root
        value={value}
        onValueChange={onValueChange}
        disabled={disabled}
      >
        <SelectPrimitive.Trigger id={id} className="st-select-trigger">
          <SelectPrimitive.Value placeholder={placeholder} />
          <SelectPrimitive.Icon>
            <ChevronDown size={15} aria-hidden="true" />
          </SelectPrimitive.Icon>
        </SelectPrimitive.Trigger>
        <SelectPrimitive.Portal>
          <SelectPrimitive.Content
            position="popper"
            sideOffset={6}
            collisionPadding={12}
            className="st-select-content"
          >
            <SelectPrimitive.Viewport className="st-select-viewport">
              {items.map((item) => (
                <SelectPrimitive.Item
                  key={item.value}
                  value={item.value}
                  className="st-select-item"
                >
                  <SelectPrimitive.ItemText>
                    {item.label}
                  </SelectPrimitive.ItemText>
                  <SelectPrimitive.ItemIndicator>
                    <Check size={15} aria-hidden="true" />
                  </SelectPrimitive.ItemIndicator>
                </SelectPrimitive.Item>
              ))}
            </SelectPrimitive.Viewport>
          </SelectPrimitive.Content>
        </SelectPrimitive.Portal>
      </SelectPrimitive.Root>
    </div>
  );
}
