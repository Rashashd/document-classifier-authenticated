import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getBatch, listBatchPredictions } from "@/api/batches";
import { getOverlayUrl, updatePrediction } from "@/api/predictions";
import type { BatchRead, BatchStatus, DocumentLabel, PredictionRead } from "@/api/types";
import { DOCUMENT_LABELS } from "@/api/types";
import { useAuth } from "@/contexts/AuthContext";
import { ApiClientError } from "@/api/client";
import { useToast } from "@/hooks/use-toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ArrowLeft, Pencil, RefreshCw } from "lucide-react";

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
  const { user } = useAuth();
  const { toast } = useToast();
  const [batch, setBatch] = useState<BatchRead | null>(null);
  const [predictions, setPredictions] = useState<PredictionRead[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [relabelTarget, setRelabelTarget] = useState<PredictionRead | null>(null);
  const [newLabel, setNewLabel] = useState<DocumentLabel | "">("");
  const [isSaving, setIsSaving] = useState(false);
  const [overlayUrl, setOverlayUrl] = useState<string | null>(null);

  const isReviewer = user?.role === "reviewer" || user?.role === "admin";

  const fetchBatch = async () => {
    if (!id) return;
    setIsLoading(true);
    try {
      const [batchData, predsData] = await Promise.all([
        getBatch(id),
        listBatchPredictions(id),
      ]);
      setBatch(batchData);
      setPredictions(predsData.items);
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

  const openRelabel = (p: PredictionRead) => {
    setRelabelTarget(p);
    setNewLabel(p.label);
    setOverlayUrl(null);
    if (p.overlay_path) {
      getOverlayUrl(p.id).then(setOverlayUrl).catch(() => {});
    }
  };

  const closeRelabel = () => {
    if (overlayUrl) URL.revokeObjectURL(overlayUrl);
    setOverlayUrl(null);
    setRelabelTarget(null);
  };

  const handleRelabel = async () => {
    if (!relabelTarget || !newLabel) return;
    setIsSaving(true);
    try {
      const updated = await updatePrediction(relabelTarget.id, { label: newLabel });
      setPredictions((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      toast({ title: "Prediction relabelled." });
      setRelabelTarget(null);
    } catch (err) {
      const msg = err instanceof ApiClientError ? err.detail : "Relabel failed.";
      toast({ variant: "destructive", title: msg });
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading || !batch) {
    return <p className="text-muted-foreground">Loading…</p>;
  }

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
                {isReviewer && <TableHead className="w-10" />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {predictions.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={isReviewer ? 5 : 4} className="text-center text-muted-foreground py-8">
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
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <ConfidenceBar value={p.confidence} />
                        {p.confidence < 0.7 && (
                          <Badge variant="warning" className="text-[10px]">low</Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(p.created_at).toLocaleString()}
                    </TableCell>
                    {isReviewer && (
                      <TableCell>
                        {p.confidence < 0.7 && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() => openRelabel(p)}
                            title="Relabel"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                        )}
                      </TableCell>
                    )}
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={!!relabelTarget} onOpenChange={(open) => { if (!open) closeRelabel(); }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Relabel Prediction</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground font-mono">{relabelTarget?.filename}</p>

            {overlayUrl ? (
              <img
                src={overlayUrl}
                alt="Document overlay"
                className="w-full rounded border object-contain max-h-64"
              />
            ) : relabelTarget?.overlay_path ? (
              <div className="w-full h-32 rounded border bg-muted flex items-center justify-center text-xs text-muted-foreground">
                Loading preview…
              </div>
            ) : null}

            <p className="text-sm">
              Current label:{" "}
              <span className="font-medium capitalize">
                {relabelTarget?.label.replace(/_/g, " ")}
              </span>
              {" "}({Math.round((relabelTarget?.confidence ?? 0) * 100)}% confidence)
            </p>
            <Select value={newLabel} onValueChange={(v) => setNewLabel(v as DocumentLabel)}>
              <SelectTrigger>
                <SelectValue placeholder="Select new label" />
              </SelectTrigger>
              <SelectContent>
                {DOCUMENT_LABELS.map((label) => (
                  <SelectItem key={label} value={label}>
                    {label.replace(/_/g, " ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeRelabel}>Cancel</Button>
            <Button onClick={handleRelabel} disabled={isSaving || !newLabel}>
              {isSaving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
