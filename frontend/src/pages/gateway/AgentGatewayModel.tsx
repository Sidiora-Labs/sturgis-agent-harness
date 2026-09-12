import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Check, Network } from "lucide-react";
import { Badge, Button, Card, EmptyState } from "../../components/ui";
import { useAgent } from "../../hooks/useAgent";
import { api } from "../../lib/api";
import type { GatewayModel, Provider } from "../../lib/types";
import { QueryState } from "../shared";
import { useGatewayQuery } from "./useGateway";
import "./gateway.css";

/** Agents consume the installation gateway; they never own its providers. */
export default function AgentGatewayModel({
  providers,
  onOpenGateway,
}: {
  providers: Provider[];
  onOpenGateway?: () => void;
}) {
  const { accountId, agentId, notify } = useAgent();
  const models = useGatewayQuery<GatewayModel[]>(accountId, "models");
  const client = useQueryClient();
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const active = providers.find((provider) => provider.active);
  async function select(model: string) {
    setPending(model);
    setError("");
    try {
      await api(`/agents/${agentId}/gateway-model`, { model });
      await client.invalidateQueries({ queryKey: ["agent", agentId] });
      notify("Gateway model selected. Your agent is ready.");
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not select this model. Check the gateway connection and try again.",
      );
    } finally {
      setPending(null);
    }
  }
  return (
    <Card
      title="Your agent’s model"
      description="Choose a model from your gateway. Provider connections are managed once for your whole installation."
      actions={
        onOpenGateway && (
          <Button onClick={onOpenGateway}>
            <Network size={16} />
            Open Model Gateway
            <ArrowUpRight size={15} />
          </Button>
        )
      }
    >
      <QueryState
        loading={models.isPending}
        error={models.error}
        retry={() => models.refetch()}
      />
      {active && (
        <p className="gateway-active-model">
          <Check size={16} />
          <span>
            Currently using <strong>{active.model}</strong>
          </span>
        </p>
      )}
      {models.data?.length
        ? models.data.map((model) => (
            <article className="record row spread" key={model.id}>
              <div>
                <h3>{model.name}</h3>
                <p className="record-subtitle">{model.provider_name}</p>
              </div>
              {active?.model === model.id ? (
                <Badge tone="accent">Selected</Badge>
              ) : (
                <Button
                  size="sm"
                  variant="primary"
                  loading={pending === model.id}
                  disabled={!!pending}
                  onClick={() => select(model.id)}
                >
                  Use model
                </Button>
              )}
            </article>
          ))
        : !models.isPending && (
            <EmptyState
              icon={<Network size={25} />}
              title="Connect a provider to get started"
              description="Open the Model Gateway, pick a provider, and import its models. Then choose one here."
              action={
                onOpenGateway && (
                  <Button variant="primary" onClick={onOpenGateway}>
                    Connect a provider
                  </Button>
                )
              }
            />
          )}
      {error && (
        <p className="gateway-error" role="alert">
          {error}
        </p>
      )}
    </Card>
  );
}
