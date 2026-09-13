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
  const [failure, setFailure] = useState<{
    model: string;
    message: string;
  } | null>(null);
  const active = providers.find((provider) => provider.active);
  async function select(model: string) {
    setPending(model);
    setFailure(null);
    try {
      const selected = await api<Provider>(`/agents/${agentId}/gateway-model`, {
        model,
      });
      await client.invalidateQueries({ queryKey: ["agent", agentId] });
      notify(
        selected.tools
          ? "Gateway model selected. Chat and tools are ready."
          : "Gateway model selected for chat only. Tool use is not verified.",
      );
    } catch (caught) {
      setFailure({
        model,
        message:
          caught instanceof Error
            ? caught.message
            : "Could not select this model. Check the gateway connection and try again.",
      });
      await client.invalidateQueries({ queryKey: ["agent", agentId] });
    } finally {
      setPending(null);
    }
  }
  return (
    <Card
      title="Your agent’s model"
      description="Choose an imported model. Sturgis checks that it is ready before selecting it for your agent."
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
        <div>
          <p className="gateway-active-model">
            <Check size={16} />
            <span>
              Currently using <strong>{active.model}</strong>{" "}
              <Badge tone={active.tools ? "accent" : "warning"}>
                {active.tools ? "Chat and tools" : "Chat only"}
              </Badge>
            </span>
          </p>
          {!active.tools && (
            <p className="record-subtitle">
              This model can reply, but tool use has not been verified. Retry
              the tool check or choose a model that supports tools to use
              actions.
            </p>
          )}
        </div>
      )}
      {models.data?.length
        ? models.data.map((model) => {
            const selected = active?.model === model.id;
            const retryTools = selected && !active.tools;
            return (
              <article className="record" key={model.id}>
                <div className="row spread">
                  <div>
                    <h3>{model.name}</h3>
                    <p className="record-subtitle">{model.provider_name}</p>
                  </div>
                  <div className="row">
                    {selected && failure?.model !== model.id && (
                      <Badge tone="accent">Selected</Badge>
                    )}
                    {(!selected ||
                      retryTools ||
                      failure?.model === model.id) && (
                      <Button
                        size="sm"
                        variant="primary"
                        loading={pending === model.id}
                        disabled={!!pending}
                        onClick={() => select(model.id)}
                      >
                        {pending === model.id
                          ? "Checking model…"
                          : failure?.model === model.id
                            ? "Retry"
                            : retryTools
                              ? "Retry tool check"
                              : "Use model"}
                      </Button>
                    )}
                  </div>
                </div>
                {failure?.model === model.id && (
                  <div className="gateway-error" role="alert">
                    <strong>Could not use {model.name}</strong>
                    <p>{failure.message}</p>
                  </div>
                )}
              </article>
            );
          })
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
    </Card>
  );
}
