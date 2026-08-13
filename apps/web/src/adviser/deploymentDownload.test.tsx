import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { DeploymentExport, RecommendationRequest } from "../api";

const fetchDeploymentExport = vi.fn();
const downloadZip = vi.fn();

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    fetchDeploymentExport: (...args: unknown[]) => fetchDeploymentExport(...args),
  };
});

vi.mock("./zip", () => ({
  downloadZip: (...args: unknown[]) => downloadZip(...args),
}));

// Imported AFTER the mocks are registered.
import { DeploymentDownload } from "./DeploymentDownload";

const REQUEST: RecommendationRequest = {
  workload_name: "My App",
  requirements: [
    {
      category: "relational-databases",
      demands: [{ metric: "storage", amount: "1", unit: "GB", period: "month" }],
    },
  ],
};

const EXPORT: DeploymentExport = {
  workload_name: "My App",
  fully_zero_cost: true,
  files: [
    { path: "docker-compose.yml", content: "services:\n  app: {}\n", sha256: "a", size: 20 },
    { path: ".env.example", content: "APP_PORT=8080\n", sha256: "b", size: 14 },
    { path: "MANIFEST.json", content: "{}", sha256: "c", size: 2 },
  ],
  manifest: {
    schema_version: 1,
    generator: "freetier-atlas-deployment-export/1",
    workload_name: "My App",
    fully_zero_cost: true,
    platforms: ["linux/amd64", "linux/arm64"],
    files: [
      { path: ".env.example", sha256: "b", size: 14 },
      { path: "docker-compose.yml", sha256: "a", size: 20 },
    ],
    total_bytes: 36,
    file_count: 2,
    validation: { paths_safe: true, secret_scan_passed: true, multi_arch: true },
    architecture: [],
    self_hosting_required: [],
    notes: ["Nothing is persisted server-side."],
  },
};

afterEach(() => {
  cleanup();
  fetchDeploymentExport.mockReset();
  downloadZip.mockReset();
});

describe("DeploymentDownload — browser-side ZIP control (F007 slice 3)", () => {
  it("exposes a labelled, keyboard-operable button and honest secret-free copy", () => {
    render(<DeploymentDownload request={REQUEST} />);
    const button = screen.getByRole("button", { name: /download deployment/i });
    expect(button).toBeTruthy();
    // Honest, up-front statement that no secrets are included.
    expect(screen.getByText(/no secrets are ever included/i)).toBeTruthy();
  });

  it("fetches the validated export and assembles the .zip client-side on click", async () => {
    fetchDeploymentExport.mockResolvedValue(EXPORT);
    render(<DeploymentDownload request={REQUEST} />);

    fireEvent.click(screen.getByRole("button", { name: /download deployment/i }));

    await waitFor(() => expect(downloadZip).toHaveBeenCalledTimes(1));
    // The same structured request is sent to the export endpoint.
    expect(fetchDeploymentExport).toHaveBeenCalledWith(REQUEST, expect.anything());
    // The browser zips exactly the server-validated files (path+content only).
    const [entries, filename] = downloadZip.mock.calls[0];
    expect(entries).toEqual([
      { path: "docker-compose.yml", content: "services:\n  app: {}\n" },
      { path: ".env.example", content: "APP_PORT=8080\n" },
      { path: "MANIFEST.json", content: "{}" },
    ]);
    expect(filename).toMatch(/freetier-atlas-my-app\.zip/);

    // Manifest summary is rendered verbatim (files, platforms, validation).
    expect(await screen.findByText(/download has started/i)).toBeTruthy();
    expect(screen.getByText(/persisted nothing/i)).toBeTruthy();
    expect(screen.getByText(/linux\/amd64, linux\/arm64/i)).toBeTruthy();
  });

  it("surfaces an error without claiming server persistence", async () => {
    fetchDeploymentExport.mockRejectedValue(
      new Error("The requirements were rejected by the API."),
    );
    render(<DeploymentDownload request={REQUEST} />);

    fireEvent.click(screen.getByRole("button", { name: /download deployment/i }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(/nothing was written on the server/i)).toBeTruthy();
    expect(downloadZip).not.toHaveBeenCalled();
  });
});
