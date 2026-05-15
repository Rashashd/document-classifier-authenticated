import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getBatch } from "@/api/batches";
import type { BatchRead, BatchStatus, PredictionRead } from "@/api/types";
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
import { ArrowLeft, RefreshCw } from "lucide-react";

const STATUS_VARIANTS: Record<BatchStatus, "default" | "secondary" | "warning" | "success" | "destructive"> = {
  pending: "secondary",
  processing: "warning",
  done: "success",
  failed: "destructive",
};

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = value >= 0.7 ? "bg-green-500" : value >= 0.4 ? "bg-yellow-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 rounded-full bg-muted overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-muted-foreground">{pct}%</span>
    </div>
  );
}

export function BatchDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [batch, setBatch] = useState<(BatchRead & { predictions?: PredictionRead[] }) | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchBatch = async () => {
    if (!id) return;
    setIsLoading(true);
    try {
      const data = await getBatch(id);
      setBatch(data as BatchRead & { predictions?: PredictionRead[] });
    } catch (err) {
      const msg = err instanceof ApiClientError && err.status === 404
        ? "Batch not found."
        : "Failed to load batch.";
      toast({ variant: "destructive", title: msg });
      navigate("/batches");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => { fetchBatch(); }, [id]);

  if (isLoading || !batch) {
    return <p className="text-muted-foreground">Loading…</p>;
  }

  const predictions: PredictionRead[] = (batch as { predictions?: PredictionRead[] }).predictions ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/batches")}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">Batch Detail</h1>
          <p className="font-mono text-xs text-muted-foreground">{batch.sftp_path}</p>
        </div>
        <Button variant="outline" size="sm" onClick={fetchBatch}>
          <RefreshCw className="h-4 w-4" />
          Refresh
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground uppercase">Status</CardTitle></CardHeader>
          <CardContent>
            <Badge variant={STATUS_VARIANTS[batch.status]} className="capitalize">{batch.status}</Badge>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground uppercase">Documents</CardTitle></CardHeader>
          <CardContent><p className="text-2xl font-bold">{batch.document_count}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground uppercase">Created</CardTitle></CardHeader>
          <CardContent><p className="text-sm">{new Date(batch.created_at).toLocaleString()}</p></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-xs text-muted-foreground uppercase">Updated</CardTitle></CardHeader>
          <CardContent><p className="text-sm">{new Date(batch.updated_at).toLocaleString()}</p></CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Predictions ({predictions.length})</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Filename</TableHead>
                <TableHead>Label</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {predictions.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="text-center text-muted-foreground py-8">
                    No predictions yet.
                  </TableCell>
                </TableRow>
              ) : (
                predictions.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="font-mono text-xs">{p.filename}</TableCell>
                    <TableCell>
                      <span className="capitalize text-sm">{p.label.replace(/_/g, " ")}</span>
                    </TableCell>
                    <TableCell><ConfidenceBar value={p.confidence} /></TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(p.created_at).toLocaleString()}
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
