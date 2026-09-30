/**
 * UI 冒烟（tasks S9 验收：关键路径 UI 冒烟通过）。
 * mock ./api —— 断言界面行为（渲染、导航、调用共享服务边界），不测传输层。
 * 注意：vi.mock 工厂被提升执行，须用内联 vi.fn，不得引用外部 const。
 */
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup); // vitest 未开 globals，RTL 自动清理不生效，需显式清理

vi.mock("./api", () => ({
  client: {
    listPlans: vi.fn().mockResolvedValue({ plans: [] }),
    createPlan: vi.fn().mockResolvedValue({
      plan: { plan_id: "sales-delta-x", plan_version: 1 },
      contract: {},
      unresolved: [],
      status: "DRAFT",
    }),
    validate: vi.fn(),
    bind: vi.fn(),
    compile: vi.fn(),
    approve: vi.fn(),
  },
}));

import { App } from "./app-root";

const TABS = ["项目与模板", "分析计划", "数据与口径", "检查与确认", "运行与验收"];

describe("Analysis Plan Studio UI 冒烟", () => {
  it("渲染五个主要页面入口", async () => {
    render(<App />);
    for (const label of TABS) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }
    expect(await screen.findByRole("heading", { name: "项目与模板" })).toBeTruthy();
  });

  it("从模板创建草稿并推进到数据页", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "分析计划" }));
    fireEvent.click(screen.getByRole("button", { name: "从模板创建草稿" }));
    // onCreated 异步完成后跳转数据页
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "数据与口径" })).toBeTruthy()
    );
    // 无路径时绑定按钮禁用（未绑定/不可执行状态如实呈现）
    const bindButton = screen.getByRole("button", { name: /校验并绑定/ });
    expect(bindButton).toHaveProperty("disabled", true);
  });

  it("无任务时数据页显示引导而非空白", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "数据与口径" }));
    expect(screen.getByText(/先在「项目与模板」/)).toBeTruthy();
  });
});
