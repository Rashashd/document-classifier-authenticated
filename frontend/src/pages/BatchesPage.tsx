import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listBatches, updateBatch } from "@/api/batches";
import type { BatchRead, BatchStatus } from "@/api/types";
import { useAuth } from "@/contexts/AuthContext";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RefreshCw } from "lucide-react";

const STATUS_VARIANTS: Record<BatchStatus, "default" | "secondary" | "warning" | "success" | "destructive"> = {
  pending: "secondary",
  processing: "warning",
  done: "success",
  failed: "destructive",
};

export function BatchesPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [batches, setBatches] = useState<BatchRead[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [updatingId, setUpdatingId] = useState<string | null>(null);

  const isAdmin = user?.role === "admin";

  const fetchBatches = async () => {
    setIsLoading(true);
    try {
      const res = await listBatches(0, 100);
      setBatches(res.items);
      setTotal(res.total);
    } catch {
      toast({ variant: "destructive", title: "Failed to load batches." });
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => { fetchBatches(); }, []);

  const handleStatusChange = async (batch: BatchRead, status: BatchStatus) => {
    setUpdatingId(batch.id);
    try {
      const updated = await updateBatch(batch.id, { status });
      setBatches((prev) => prev.map((b) => (b.id === updated.id ? updated : b)));
      toast({ title: "Batch status updated." });
    } catch (err) {
      const msg = err instanceof ApiClientError ? err.detail : "Update failed.";
      toast({ variant: "destructive", title: msg });
    } finally {
      setUpdatingId(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Batches</h1>
          <p className="text-sm text-muted-foreground">{total} total</p>
        </div>
        <Button variant="outline" size="sm" onClick={fetchBatches} disabled={isLoading}>
          <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">All Batches</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>SFTP Path</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Docs</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Updated</TableHead>
                {isAdmin && <TableHead>Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={isAdmin ? 6 : 5} className="text-center text-muted-foreground py-8">
                    Loading…
                  </TableCell>
                </TableRow>
              ) : batches.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={isAdmin ? 6 : 5} className="text-center text-muted-foreground py-8">
                    No batches yet.
                  </TableCell>
                </TableRow>
              ) : (
                batches.map((batch) => (
                  <TableRow
                    key={batch.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/batches/${batch.id}`)}
                  >
                    <TableCell className="font-mono text-xs max-w-[200px] truncate">
                      {batch.sftp_path}
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANTS[batch.status]} className="capitalize">
                        {batch.status}
                      </Badge>
                    </TableCell>
                    <TableCell>{batch.document_count}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(batch.created_at).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(batch.updated_at).toLocaleString()}
                    </TableCell>
                    {isAdmin && (
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <Select
                          value={batch.status}
                          onValueChange={(v) => handleStatusChange(batch, v as BatchStatus)}
                          disabled={updatingId === batch.id}
                        >
                          <SelectTrigger className="w-32 h-7 text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="pending">pending</SelectItem>
                            <SelectItem value="processing">processing</SelectItem>
                            <SelectItem value="done">done</SelectItem>
                            <SelectItem value="failed">failed</SelectItem>
                          </SelectContent>
                        </Select>
                      </TableCell>
                    )}
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
