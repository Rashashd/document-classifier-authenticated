import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Clock,
  Cpu,
  ExternalLink,
  Layers,
  RefreshCw,
  Zap,
} from "lucide-react";
import { triggerDemo, getQueueStats, getDemoBatches } from "@/api/demo";
import type { QueueStats } from "@/api/demo";
import type { BatchRead, BatchStatus } from "@/api/types";
import { ApiClientError } from "@/api/client";
import { useToast } from "@/hooks/use-toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

const STATUS_VARIANTS: Record<
  BatchStatus,
  "default" | "secondary" | "warning" | "success" | "destructive"
> = {
  pending: "secondary",
  processing: "warning",
  done: "success",
  failed: "destructive",
};

function timeAgo(dateStr: string): string {
  const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (diff < 5) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

interface StageBoxProps {
  icon: React.ReactNode;
  label: string;
  count: number;
  colorClass: string;
  bgClass: string;
}

function StageBox({ icon, label, count, colorClass, bgClass }: StageBoxProps) {
  return (
    <div className={cn("flex-1 rounded-lg border p-4 text-center min-w-0", bgClass)}>
      <div className={cn("flex justify-center mb-2", colorClass)}>{icon}</div>
      <div className={cn("text-3xl font-bold tabular-nums", colorClass)}>{count}</div>
      <div className="text-xs text-muted-foreground mt-1 font-medium">{label}</div>
    </div>
  );
}

export function DemoPage() {
  const [stats, setStats] = useState<QueueStats>({
    pending: 0,
    processing: 0,
    done: 0,
    failed: 0,
  });
  const [batches, setBatches] = useState<BatchRead[]>([]);
  const [triggering, setTriggering] = useState(false);
  const [triggeredIds, setTriggeredIds] = useState<Set<string>>(new Set());
  const [triggeredList, setTriggeredList] = useState<string[]>([]);
  const { toast } = useToast();
  const navigate = useNavigate();

  const refresh = useCallback(async () => {
    try {
      const [queueStats, batchData] = await Promise.all([
        getQueueStats(),
        getDemoBatches(20),
      ]);
      setStats(queueStats);
      setBatches(batchData.items);
    } catch {
      // silent on polling errors — avoid spamming toasts
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [refresh]);

  const handleTrigger = async () => {
    setTriggering(true);
    try {
      const result = await triggerDemo();
      setTriggeredIds((prev) => new Set([...prev, result.batch_id]));
      setTriggeredList((prev) => [result.batch_id, ...prev]);
      toast({ title: `Injected: ${result.filename}` });
      await refresh();
    } catch (err) {
      const msg =
        err instanceof ApiClientError ? err.detail : "Failed to inject document.";
      toast({ variant: "destructive", title: msg });
    } finally {
      setTriggering(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Pipeline Demo</h1>
        <p className="text-sm text-muted-foreground">
          Inject synthetic documents and watch them flow through the classification
          pipeline in real time.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Inject card */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Zap className="h-4 w-4" />
              Inject Document
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Generates a synthetic TIFF and enqueues it for ML classification
              without needing SFTP access.
            </p>
            <Button
              onClick={handleTrigger}
              disabled={triggering}
              className="w-full"
            >
              {triggering ? (
                <RefreshCw className="h-4 w-4 animate-spin mr-2" />
              ) : (
                <Zap className="h-4 w-4 mr-2" />
              )}
              {triggering ? "Injecting…" : "Generate & Inject"}
            </Button>

            {triggeredList.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  This session
                </p>
                {triggeredList.slice(0, 5).map((id) => (
                  <button
                    key={id}
                    onClick={() => navigate(`/batches/${id}`)}
                    className="block w-full text-left font-mono text-xs text-primary hover:underline truncate"
                  >
                    {id}
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Pipeline stages */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="h-4 w-4" />
              Pipeline Status
              <span className="ml-auto text-xs font-normal text-muted-foreground">
                Live · polling every 3s
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <StageBox
                icon={<Clock className="h-5 w-5" />}
                label="Queued"
                count={stats.pending}
                colorClass="text-slate-600"
                bgClass="bg-slate-50 border-slate-200"
              />
              <ChevronRight className="h-5 w-5 text-muted-foreground shrink-0" />
              <StageBox
                icon={<Cpu className="h-5 w-5" />}
                label="Processing"
                count={stats.processing}
                colorClass="text-yellow-700"
                bgClass="bg-yellow-50 border-yellow-200"
              />
              <ChevronRight className="h-5 w-5 text-muted-foreground shrink-0" />
              <StageBox
                icon={<CheckCircle2 className="h-5 w-5" />}
                label="Done"
                count={stats.done}
                colorClass="text-green-700"
                bgClass="bg-green-50 border-green-200"
              />
              {stats.failed > 0 && (
                <>
                  <ChevronRight className="h-5 w-5 text-muted-foreground shrink-0" />
                  <StageBox
                    icon={<AlertCircle className="h-5 w-5" />}
                    label="Failed"
                    count={stats.failed}
                    colorClass="text-red-700"
                    bgClass="bg-red-50 border-red-200"
                  />
                </>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Live batch feed */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Layers className="h-4 w-4" />
            Live Batch Feed
            <span className="ml-auto text-xs font-normal text-muted-foreground">
              Last 20 batches · click any row to view details
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Document</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Queued</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {batches.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={5}
                    className="text-center text-muted-foreground py-10"
                  >
                    No batches yet. Click "Generate &amp; Inject" to start the pipeline.
                  </TableCell>
                </TableRow>
              ) : (
                batches.map((batch) => (
                  <TableRow
                    key={batch.id}
                    className={cn(
                      "cursor-pointer",
                      triggeredIds.has(batch.id) && "bg-primary/5 hover:bg-primary/10"
                    )}
                    onClick={() => navigate(`/batches/${batch.id}`)}
                  >
                    <TableCell className="font-mono text-xs max-w-[240px] truncate">
                      {batch.sftp_path.split("/").pop() || batch.sftp_path}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={STATUS_VARIANTS[batch.status]}
                        className="capitalize"
                      >
                        {batch.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {timeAgo(batch.created_at)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {timeAgo(batch.updated_at)}
                    </TableCell>
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      {batch.status === "done" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0"
                          onClick={() => navigate(`/batches/${batch.id}`)}
                          title="View batch details"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
