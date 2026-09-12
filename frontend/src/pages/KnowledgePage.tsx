import { useState } from "react";
import {
  BookOpen,
  Brain,
  Download,
  FileText,
  Plus,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Modal,
  Tabs,
} from "../components/ui";
import { useAction, useAgent, useAgentQuery } from "../hooks/useAgent";
import { fileBase64 } from "../lib/api";
import type {
  Knowledge,
  MemoryRecord,
  MemoryResult,
  Page,
  Source,
} from "../lib/types";
import { Evidence, FormDialog, PageHeader, QueryState } from "./shared";
export default function KnowledgePage() {
  const { agentId, notify } = useAgent();
  const { execute, pending } = useAction();
  const [tab, setTab] = useState("library");
  const [uploading, setUploading] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [reading, setReading] = useState<Knowledge | null>(null);
  const [remove, setRemove] = useState<Knowledge | null>(null);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [edit, setEdit] = useState<MemoryRecord | null>(null);
  const [retract, setRetract] = useState<MemoryRecord | null>(null);
  const sources = useAgentQuery<Page<Source>>("attachments.list");
  const documents = useAgentQuery<Page<Knowledge>>("knowledge.list");
  const memories = useAgentQuery<MemoryResult>(
    "memory.query",
    { text: query },
    { enabled: tab === "memory" },
  );
  const addFile = async () => {
    if (!file) return;
    try {
      const encoded = await fileBase64(file);
      const result = await execute<Source>(
        "attachments.upload",
        {
          name: file.name,
          media_type: file.type || "application/octet-stream",
          data_base64: encoded,
        },
        "Source uploaded to your private library.",
      );
      if (result) {
        setUploading(false);
        setFile(null);
      }
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };
  return (
    <div className="page">
      <PageHeader
        eyebrow="A source of continuity"
        title="Knowledge"
        description="Give your agent context you can trace, inspect, and refine."
        actions={
          <Button variant="primary" onClick={() => setUploading(true)}>
            <Plus size={18} />
            Add a source
          </Button>
        }
      />
      <Tabs
        value={tab}
        onValueChange={setTab}
        items={[
          { value: "library", label: "Source library" },
          { value: "memory", label: "Agent memory" },
        ]}
      />
      <div className="mt-7">
        {tab === "library" ? (
          <>
            <QueryState
              loading={sources.isPending || documents.isPending}
              error={sources.error || documents.error}
              retry={() => {
                sources.refetch();
                documents.refetch();
              }}
            />
            {sources.data && documents.data && (
              <div className="page-grid">
                <Card
                  title="Your sources"
                  description="Original files stay available, alongside their extraction status."
                >
                  {sources.data.items.length ? (
                    <div className="record-list">
                      {sources.data.items.map((source) => (
                        <article className="record" key={source.id}>
                          <div className="row spread">
                            <div className="row">
                              <FileText size={21} className="muted" />
                              <div>
                                <h3 className="record-title">{source.name}</h3>
                                <p className="record-subtitle">
                                  {source.media_type} ·{" "}
                                  {(source.bytes / 1024).toFixed(1)} KiB
                                </p>
                              </div>
                            </div>
                            <Badge
                              tone={
                                source.extraction.status === "ready"
                                  ? "success"
                                  : "warning"
                              }
                            >
                              {source.extraction.status}
                            </Badge>
                          </div>
                          <div className="record-actions">
                            <Button
                              size="sm"
                              onClick={() =>
                                execute(
                                  "knowledge.add",
                                  {
                                    attachment_id: source.id,
                                    title: source.name,
                                  },
                                  "Source added to knowledge.",
                                )
                              }
                              disabled={
                                pending || source.extraction.status !== "ready"
                              }
                            >
                              <BookOpen size={15} />
                              Add to knowledge
                            </Button>
                            <a
                              className="st-button st-button--ghost st-button--sm"
                              href={`/api/v1/agents/${agentId}/attachments/${source.id}`}
                              download={source.name}
                            >
                              <Download size={15} />
                              Download
                            </a>
                          </div>
                          <Evidence data={source} />
                        </article>
                      ))}
                    </div>
                  ) : (
                    <EmptyState
                      icon={<Upload size={25} />}
                      title="Good context starts here"
                      description="Add notes, documents, and source material. Supported text becomes readable context with exact source references."
                      action={
                        <Button onClick={() => setUploading(true)}>
                          Upload your first source
                        </Button>
                      }
                    />
                  )}
                </Card>
                <Card
                  title="Connected knowledge"
                  description="Indexed text, backed by the original source."
                >
                  {documents.data.items.length ? (
                    documents.data.items.map((document) => (
                      <article className="record" key={document.id}>
                        <h3 className="record-title">{document.title}</h3>
                        <p className="record-subtitle">
                          Revision {document.revision} ·{" "}
                          {document.memory_ids.length} memory records
                        </p>
                        <div className="record-actions">
                          <Button
                            size="sm"
                            onClick={async () => {
                              const result = await execute<Knowledge>(
                                "knowledge.get",
                                { id: document.id },
                              );
                              if (result) setReading(result);
                            }}
                          >
                            <BookOpen size={15} />
                            Read source
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={
                              "Remove " + document.title + " from knowledge"
                            }
                            onClick={() => setRemove(document)}
                          >
                            <Trash2 size={15} />
                          </Button>
                        </div>
                        <Evidence data={document} />
                      </article>
                    ))
                  ) : (
                    <EmptyState
                      icon={<BookOpen size={25} />}
                      title="A library your agent can use"
                      description="Add an uploaded source to knowledge to index its text in native memory. The original stays in your source library."
                    />
                  )}
                </Card>
              </div>
            )}
          </>
        ) : (
          <>
            <form
              className="section-toolbar"
              onSubmit={(event) => {
                event.preventDefault();
                setQuery(search);
              }}
            >
              <Input
                aria-label="Search memory"
                placeholder="Search an idea, fact, or concept…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <Button type="submit">
                <Search size={16} />
                Search memory
              </Button>
            </form>
            <QueryState
              loading={memories.isPending}
              error={memories.error}
              retry={() => memories.refetch()}
            />
            {memories.data && (
              <Card
                title="What your agent remembers"
                description="Records have provenance and revisions. Corrections become new evidence, and retractions remain traceable."
              >
                {memories.data.status !== "ok" ? (
                  <p role="alert">
                    {memories.data.error?.message ||
                      "Native memory is not available."}
                  </p>
                ) : memories.data.data?.records.length ? (
                  memories.data.data.records.map(({ record }) => (
                    <article className="record" key={record.id}>
                      <div className="row spread">
                        <Badge>{record.kind}</Badge>
                        <span className="record-subtitle">
                          Revision {record.revision}
                        </span>
                      </div>
                      <p className="record-title whitespace-pre-wrap mt-3">
                        {record.content}
                      </p>
                      <p className="record-subtitle">
                        {record.concepts.join(" · ")}
                      </p>
                      <div className="record-actions">
                        <Button size="sm" onClick={() => setEdit(record)}>
                          Correct memory
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setRetract(record)}
                        >
                          Retract
                        </Button>
                      </div>
                      <Evidence data={record} />
                    </article>
                  ))
                ) : (
                  <EmptyState
                    icon={<Brain size={26} />}
                    title="Room to remember"
                    description={
                      query
                        ? "No current records match this search. Try another concept."
                        : "Memories appear as you add knowledge and your agent consolidates experience."
                    }
                  />
                )}
              </Card>
            )}
          </>
        )}
      </div>
      <Modal
        open={uploading}
        onOpenChange={setUploading}
        title="Add a source"
        description="Upload up to 4 MiB. Text, Markdown, CSV, JSON, and HTML support bounded text extraction."
      >
        <div
          className="upload-zone"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            setFile(event.dataTransfer.files[0] || null);
          }}
        >
          <Upload className="mx-auto mb-3 text-accent" size={28} />
          <p>{file ? file.name : "Drop a file here, or choose one below."}</p>
          <label className="field mt-4">
            Source file
            <Input
              type="file"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
            />
          </label>
        </div>
        <div className="form-actions">
          <Button variant="ghost" onClick={() => setUploading(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={pending}
            disabled={!file}
            onClick={addFile}
          >
            Upload source
          </Button>
        </div>
      </Modal>
      <Modal
        open={!!reading}
        onOpenChange={(open) => {
          if (!open) setReading(null);
        }}
        title={reading?.title || "Source"}
        description="Exact extracted source text, treated as untrusted content."
      >
        <pre className="code">{reading?.text}</pre>
      </Modal>
      <Modal
        open={!!remove}
        onOpenChange={(open) => {
          if (!open) setRemove(null);
        }}
        title="Remove from knowledge?"
        description="This retracts the document’s indexed memories. The original upload remains available."
      >
        <div className="form-actions">
          <Button variant="ghost" onClick={() => setRemove(null)}>
            Keep knowledge
          </Button>
          <Button
            variant="danger"
            loading={pending}
            onClick={async () => {
              if (
                await execute("knowledge.delete", {
                  id: remove!.id,
                  expected_revision: remove!.revision,
                })
              )
                setRemove(null);
            }}
          >
            Remove from knowledge
          </Button>
        </div>
      </Modal>
      <FormDialog
        open={!!edit}
        onOpenChange={(open) => {
          if (!open) setEdit(null);
        }}
        title="Correct this memory"
        description="Your correction is stored as a new, source-backed revision."
        fields={[
          {
            name: "content",
            label: "Corrected memory",
            type: "textarea",
            value: edit?.content,
            required: true,
          },
        ]}
        pending={pending}
        submitLabel="Save correction"
        onSubmit={async (data) => {
          const result = await execute<MemoryResult>("memory.correct", {
            id: edit!.id,
            content: data.content,
            expected_revision: edit!.revision,
          });
          if (result?.status === "ok") {
            setEdit(null);
            notify("Memory correction recorded.");
          } else if (result)
            notify(
              result.error?.message || "Memory correction failed.",
              "error",
            );
        }}
      />
      <FormDialog
        open={!!retract}
        onOpenChange={(open) => {
          if (!open) setRetract(null);
        }}
        title="Retract this memory"
        fields={[
          { name: "reason", label: "Reason", type: "textarea", required: true },
        ]}
        pending={pending}
        submitLabel="Retract memory"
        onSubmit={async (data) => {
          const result = await execute<MemoryResult>("memory.retract", {
            id: retract!.id,
            revision: retract!.revision,
            reason: data.reason,
          });
          if (result?.status === "ok") setRetract(null);
          else if (result)
            notify(
              result.error?.message || "Memory retraction failed.",
              "error",
            );
        }}
      />
    </div>
  );
}
