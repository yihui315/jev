/**
 * TopBar — connection transport state, control-token entry and the primary CTA.
 *
 * The control token is held in the app's in-memory store and typed here. It is
 * NEVER written to localStorage, a cookie, the URL or a log — which is why this
 * component takes a value/onChange pair rather than reading storage itself.
 */
import { Menu, Plus, RefreshCw, ShieldCheck, ShieldOff } from "lucide-react";
import { Button } from "../ui/Button";
import { Badge, type BadgeProps } from "../ui/Badge";
import { Input } from "../ui/Field";
import { cn } from "../ui/cn";
import { navLabel, type ViewMode } from "../app/navigation";
import type { ConnectionState } from "../store/sceneMap";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/Tooltip";

const CONN_VARIANT: Record<ConnectionState, NonNullable<BadgeProps["variant"]>> = {
  CONNECTING: "warn",
  LIVE: "live",
  RECONNECTING: "warn",
  POLLING: "info",
  OFFLINE: "danger",
};

export function TopBar({
  view,
  connection,
  controlToken,
  onControlToken,
  onNewMission,
  onRefresh,
  refreshing,
  onOpenMobileNav,
  actuationEnabled,
}: {
  view: ViewMode;
  connection: ConnectionState;
  controlToken: string;
  onControlToken: (v: string) => void;
  onNewMission: () => void;
  onRefresh: () => void;
  refreshing: boolean;
  onOpenMobileNav: () => void;
  actuationEnabled: boolean | null;
}) {
  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-border bg-card px-4 py-2.5">
      <button
        onClick={onOpenMobileNav}
        className="rounded-md p-1.5 text-muted-foreground hover:bg-accent lg:hidden"
        aria-label="Open navigation"
      >
        <Menu className="h-4 w-4" />
      </button>

       <div className="flex min-w-0 items-center gap-2">
        <h1 className="truncate text-sm font-semibold text-foreground">
          {navLabel(view)}
        </h1>
        <Badge variant={CONN_VARIANT[connection] ?? "unknown"}>
          {connection}
        </Badge>
      </div>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        {actuationEnabled === false ? (
          <TooltipProvider delayDuration={150}>
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="ok" className="gap-1">
                  <ShieldCheck className="h-3 w-3" />
                  Actuation OFF (fail-closed)
                </Badge>
              </TooltipTrigger>
              <TooltipContent>
                No effectful action is possible in this build. Policy may emit a
                directive; no actuator is mounted.
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : actuationEnabled === true ? (
          <Badge variant="danger" className="gap-1">
            <ShieldOff className="h-3 w-3" />
            Actuation ENABLED — review required
          </Badge>
        ) : (
          <Badge variant="unknown" title="Actuation flag unknown">
            Actuation UNKNOWN
          </Badge>
        )}

        <div className="flex items-center gap-1.5">
          <label htmlFor="jcc-control-token" className="sr-only">
            Control token (memory only)
          </label>
          <Input
            id="jcc-control-token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="control token (memory only)"
            value={controlToken}
            onChange={(e) => onControlToken(e.target.value)}
            className={cn("h-8 w-52 text-xs")}
          />
        </div>

        <Button
          variant="outline"
          size="sm"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="Refresh data"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
          Refresh
        </Button>

        <Button size="sm" onClick={onNewMission} data-testid="cta-new-mission">
          <Plus className="h-3.5 w-3.5" />
          New Mission
        </Button>
      </div>
    </header>
  );
}
