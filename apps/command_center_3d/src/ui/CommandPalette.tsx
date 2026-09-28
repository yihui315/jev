/**
 * CommandPalette — cmdk + Radix Dialog (⌘K / Ctrl+K).
 *
 * The palette is a *navigation* surface: it can move the product between
 * views and open the New Mission studio. It deliberately holds NO mutating
 * action, so a keyboard shortcut can never become a side-effect channel.
 */
import * as React from "react";
import { Command } from "cmdk";
import { Dialog, DialogContent, DialogTitle } from "./Dialog";
import { cn } from "./cn";
import type { LucideIcon } from "lucide-react";

export interface CommandItemSpec {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon?: LucideIcon;
  keywords?: string[];
  onSelect: () => void;
}

export function CommandPalette({
  open,
  onOpenChange,
  items,
  placeholder = "Search views, projects, missions…",
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  items: CommandItemSpec[];
  placeholder?: string;
}) {
  const groups = React.useMemo(() => {
    const m = new Map<string, CommandItemSpec[]>();
    for (const it of items) {
      const arr = m.get(it.group) ?? [];
      arr.push(it);
      m.set(it.group, arr);
    }
    return [...m.entries()];
  }, [items]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl p-0">
        {/* Radix requires a title for screen-reader users; the visible heading
            would be noise in a spotlight dialog, so it is rendered only to AT. */}
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <Command
          className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-muted-foreground"
          loop
        >
          <div className="flex items-center border-b border-border px-3">
            <Command.Input
              autoFocus
              placeholder={placeholder}
              className="h-11 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
            />
          </div>
          <Command.List className="max-h-[340px] overflow-y-auto p-2">
            <Command.Empty className="px-3 py-6 text-center text-xs text-muted-foreground">
              No matching command.
            </Command.Empty>
            {groups.map(([group, list]) => (
              <Command.Group key={group} heading={group}>
                {list.map((it) => {
                  const Icon = it.icon;
                  return (
                    <Command.Item
                      key={it.id}
                      value={`${it.label} ${(it.keywords ?? []).join(" ")}`}
                      onSelect={() => {
                        onOpenChange(false);
                        it.onSelect();
                      }}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm text-foreground",
                        "data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground",
                      )}
                    >
                      {Icon ? <Icon className="h-4 w-4 text-muted-foreground" /> : null}
                      <span className="flex-1 truncate">{it.label}</span>
                      {it.hint ? (
                        <span className="text-[10px] uppercase text-muted-foreground">
                          {it.hint}
                        </span>
                      ) : null}
                    </Command.Item>
                  );
                })}
              </Command.Group>
            ))}
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
