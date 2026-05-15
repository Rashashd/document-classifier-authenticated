import { useEffect, useState } from "react";
import { listRecentPredictions, updatePrediction } from "@/api/predictions";
import type { DocumentLabel, PredictionRead } from "@/api/types";
import { DOCUMENT_LABELS } from "@/api/types";
import { useAuth } from "@/contexts/AuthContext";
import { ApiClientError } from "@/api/client";
import { useToast } from "@/hooks/use-toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { RefreshCw, Pencil } from "lucide-react";

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

export function PredictionsPage() {
  const { user } = useAuth();
  const { toast } = useToast();
  const [predictions, setPredictions] = useState<PredictionRead[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [relabelTarget, setRelabelTarget] = useState<PredictionRead | null>(null);
  const [newLabel, setNewLabel] = useState<DocumentLabel | "">("");
  const [isSaving, setIsSaving] = useState(false);

  const isReviewer = user?.role === "reviewer" || user?.role === "admin";

  const fetchPredictions = async () => {
    setIsLoading(true);
    try {
      const res = await listRecentPredictions(0, 100);
      setPredictions(res.items);
      setTotal(res.total);
    } catch {
      toast({ variant: "destructive", title: "Failed to load predictions." });
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => { fetchPredictions(); }, []);

  const openRelabel = (p: PredictionRead) => {
    setRelabelTarget(p);
    setNewLabel(p.label);
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

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Predictions</h1>
          <p className="text-sm text-muted-foreground">{total} total</p>
        </div>
        <Button variant="outline" size="sm" onClick={fetchPredictions} disabled={isLoading}>
          <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Recent Predictions</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Filename</TableHead>
                <TableHead>Label</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead>Created</TableHead>
                {isReviewer && <TableHead />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={isReviewer ? 5 : 4} className="text-center text-muted-foreground py-8">
                    Loading…
                  </TableCell>
                </TableRow>
              ) : predictions.length === 0 ? (
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

      <Dialog open={!!relabelTarget} onOpenChange={(open) => { if (!open) setRelabelTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Relabel Prediction</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground font-mono">{relabelTarget?.filename}</p>
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
            <Button variant="outline" onClick={() => setRelabelTarget(null)}>Cancel</Button>
            <Button onClick={handleRelabel} disabled={isSaving || !newLabel}>
              {isSaving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
