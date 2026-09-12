import { useState } from "react";
import {
  ChevronRight,
  Download,
  File,
  Folder,
  FolderOpen,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Modal,
  Textarea,
} from "../components/ui";
import { useAction, useAgent, useAgentQuery } from "../hooks/useAgent";
import { decodeText, downloadBody, fileBase64 } from "../lib/api";
import type { FileBody, FileEntry } from "../lib/types";
import { FormDialog, PageHeader, QueryState } from "./shared";
export default function FilesPage() {
  const [path, setPath] = useState(".");
  const [selected, setSelected] = useState<FileBody | null>(null);
  const [text, setText] = useState("");
  const [binary, setBinary] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [filter, setFilter] = useState("");
  const { notify } = useAgent();
  const { execute, pending } = useAction();
  const files = useAgentQuery<{ path: string; entries: FileEntry[] }>(
    "files.list",
    { path },
  );
  const fullPath = (name: string) => (path === "." ? name : path + "/" + name);
  async function open(entry: FileEntry) {
    const name = fullPath(entry.name);
    if (entry.directory) {
      setPath(name);
      return;
    }
    const result = await execute<FileBody>("files.read", { path: name });
    if (result) {
      setSelected(result);
      try {
        setText(decodeText(result.data_base64));
        setBinary(false);
      } catch {
        setBinary(true);
        setText("");
      }
    }
  }
  async function save() {
    if (!selected) return;
    const result = await execute<{ after_sha256: string }>(
      "files.write",
      {
        path: selected.path,
        data_base64: await fileBase64(new Blob([text])),
        expected_sha256: selected.sha256,
      },
      "File saved with a preimage check.",
    );
    if (result)
      setSelected({
        ...selected,
        sha256: result.after_sha256,
        data_base64: await fileBase64(new Blob([text])),
      });
  }
  return (
    <div className="page">
      <PageHeader
        eyebrow="Made here"
        title="Files"
        description="A private workspace for what you and your agent create."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Plus size={17} />
            New file
          </Button>
        }
      />
      <div className="section-toolbar">
        <Button variant="ghost" size="sm" onClick={() => setPath(".")}>
          <FolderOpen size={16} />
          Workspace
        </Button>
        {path !== "." && (
          <>
            <ChevronRight size={13} />
            <span className="record-title">{path}</span>
          </>
        )}
        <Input
          aria-label="Filter files"
          placeholder="Filter files…"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
      </div>
      <QueryState
        loading={files.isPending}
        error={files.error}
        retry={() => files.refetch()}
      />
      {files.data && (
        <div className="page-grid">
          <Card
            title={path === "." ? "Workspace files" : path}
            description="Paths and file operations stay inside this agent’s workspace."
          >
            {files.data.entries.length ? (
              <div className="record-list">
                {files.data.entries
                  .filter((entry) =>
                    entry.name.toLowerCase().includes(filter.toLowerCase()),
                  )
                  .map((entry) => (
                    <article className="record" key={entry.name}>
                      <button
                        className="row w-full text-left"
                        onClick={() => open(entry)}
                        aria-label={
                          (entry.directory ? "Open folder " : "Open file ") +
                          entry.name
                        }
                      >
                        {entry.directory ? (
                          <Folder className="text-accent" size={23} />
                        ) : (
                          <File className="muted" size={22} />
                        )}
                        <div className="flex-1">
                          <div className="record-title">{entry.name}</div>
                          <div className="record-subtitle">
                            {entry.directory
                              ? "Folder"
                              : (entry.bytes / 1024).toFixed(1) + " KiB"}
                          </div>
                        </div>
                        <ChevronRight className="muted" size={16} />
                      </button>
                    </article>
                  ))}
              </div>
            ) : (
              <EmptyState
                icon={<FolderOpen size={27} />}
                title="A clean workspace"
                description="Create a note or let your agent produce files here. Changes keep an exact preimage check."
                action={
                  <Button onClick={() => setCreating(true)}>
                    Create your first file
                  </Button>
                }
              />
            )}
          </Card>
          {selected ? (
            <Card
              title={selected.path}
              actions={<Badge>{binary ? "Binary file" : "Text file"}</Badge>}
              description="Saving checks that the file has not changed since you opened it."
            >
              {binary ? (
                <EmptyState
                  icon={<File size={25} />}
                  title="Download to open this file"
                  description="This file is not valid UTF-8 text. Its bytes remain available for download."
                />
              ) : (
                <label className="field">
                  File content
                  <Textarea
                    aria-label="File content"
                    className="file-editor"
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    spellCheck={false}
                  />
                </label>
              )}
              <div className="record-actions">
                {!binary && (
                  <Button variant="primary" loading={pending} onClick={save}>
                    <Save size={15} />
                    Save file
                  </Button>
                )}
                <Button
                  onClick={() => {
                    const bytes = Uint8Array.from(
                      atob(selected.data_base64),
                      (char) => char.charCodeAt(0),
                    );
                    downloadBody(
                      new Blob([bytes]),
                      selected.path.split("/").pop()!,
                    );
                  }}
                >
                  <Download size={15} />
                  Download
                </Button>
                <Button variant="ghost" onClick={() => setDeleting(true)}>
                  <Trash2 size={15} />
                  Delete file
                </Button>
              </div>
              <p className="field-hint mt-4 break-all">
                SHA-256 · {selected.sha256}
              </p>
            </Card>
          ) : (
            <Card>
              <EmptyState
                icon={<File size={27} />}
                title="Open a file to explore"
                description="Read and edit text without leaving your agent’s space. Binary files download safely."
              />
            </Card>
          )}
        </div>
      )}
      <FormDialog
        open={creating}
        onOpenChange={setCreating}
        title="Create a file"
        description="New files are created only when the chosen path is unused."
        fields={[
          {
            name: "path",
            label: "Workspace path",
            value: path === "." ? "notes.txt" : path + "/notes.txt",
            required: true,
          },
          { name: "content", label: "Initial content", type: "textarea" },
        ]}
        pending={pending}
        submitLabel="Create file"
        onSubmit={async (data) => {
          try {
            const encoded = await fileBase64(new Blob([data.content]));
            const result = await execute<{ after_sha256: string }>(
              "files.write",
              { path: data.path, data_base64: encoded },
              "File created.",
            );
            if (result) {
              setCreating(false);
              setSelected({
                path: data.path,
                sha256: result.after_sha256,
                data_base64: encoded,
                bytes: new Blob([data.content]).size,
              });
              setText(data.content);
              setBinary(false);
            }
          } catch (error) {
            notify((error as Error).message, "error");
          }
        }}
      />
      <Modal
        open={deleting}
        onOpenChange={setDeleting}
        title="Delete this file?"
        description={
          selected?.path +
          " will be removed only if its current contents match the version you opened."
        }
      >
        <div className="form-actions">
          <Button variant="ghost" onClick={() => setDeleting(false)}>
            Keep file
          </Button>
          <Button
            variant="danger"
            loading={pending}
            onClick={async () => {
              if (
                await execute(
                  "files.delete",
                  { path: selected!.path, expected_sha256: selected!.sha256 },
                  "File deleted.",
                )
              ) {
                setDeleting(false);
                setSelected(null);
              }
            }}
          >
            Delete file
          </Button>
        </div>
      </Modal>
    </div>
  );
}
