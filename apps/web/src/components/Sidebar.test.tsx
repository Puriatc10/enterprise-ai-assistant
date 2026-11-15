/**
 * The sidebar's row menu: rename and delete, and the two ways they can go wrong.
 *
 * These are interaction tests on purpose — the container is dumb, so what matters is that
 * a keystroke reaches the right callback exactly once and that Escape means cancel.
 */

import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "../services/api";
import { Sidebar } from "./Sidebar";

// This project's vitest setup does not register Testing Library's automatic cleanup, so
// without this every test renders into the previous test's DOM.
afterEach(cleanup);

function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: "c1",
    title: "بیمه‌نامه‌های من",
    created_at: "2026-11-14T10:00:00+00:00",
    updated_at: "2026-11-14T10:00:00+00:00",
    ...overrides,
  } as Conversation;
}

function renderSidebar(overrides: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  const props = {
    conversations: [conversation()],
    activeId: "c1" as string | null,
    onSelect: vi.fn(),
    onRename: vi.fn(async () => {}),
    onDelete: vi.fn(async () => {}),
    onCreate: vi.fn(),
    isCreating: false,
    loading: false,
    email: "puria@example.com",
    onSignOut: vi.fn(),
    open: false,
    onClose: vi.fn(),
    ...overrides,
  };
  render(<Sidebar {...props} />);
  return props;
}

describe("Sidebar conversation actions", () => {
  it("shows the stored title, and a placeholder when the thread has none yet", () => {
    renderSidebar({
      conversations: [conversation(), conversation({ id: "c2", title: null as unknown as string })],
    });

    expect(screen.getByText("بیمه‌نامه‌های من")).toBeTruthy();
    expect(screen.getByText("گفتگوی بدون عنوان")).toBeTruthy();
  });

  it("renames through the menu and submits the typed title once", async () => {
    const props = renderSidebar();

    fireEvent.click(screen.getByLabelText("گزینه‌های «بیمه‌نامه‌های من»"));
    fireEvent.click(screen.getByRole("menuitem", { name: "تغییر نام" }));

    const input = screen.getByLabelText("عنوان جدید گفتگو");
    fireEvent.change(input, { target: { value: "  بیمه‌نامه‌های فعال  " } });
    // Enter submits the form; the same keystroke also blurs the input, which commits.
    // One call is the assertion: two would mean a double PATCH.
    fireEvent.submit(input.closest("form") as HTMLFormElement);

    await waitFor(() => expect(props.onRename).toHaveBeenCalledTimes(1));
    expect(props.onRename).toHaveBeenCalledWith("c1", "بیمه‌نامه‌های فعال");
  });

  it("cancels a rename on Escape without calling the API", () => {
    const props = renderSidebar();

    fireEvent.click(screen.getByLabelText("گزینه‌های «بیمه‌نامه‌های من»"));
    fireEvent.click(screen.getByRole("menuitem", { name: "تغییر نام" }));

    const input = screen.getByLabelText("عنوان جدید گفتگو");
    fireEvent.change(input, { target: { value: "چیزی که منصرف شدم" } });
    fireEvent.keyDown(input, { key: "Escape" });

    expect(props.onRename).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("عنوان جدید گفتگو")).toBeNull();
  });

  it("asks before deleting, then deletes", async () => {
    const props = renderSidebar();

    fireEvent.click(screen.getByLabelText("گزینه‌های «بیمه‌نامه‌های من»"));
    fireEvent.click(screen.getByRole("menuitem", { name: "حذف" }));
    // Nothing is deleted on the first click.
    expect(props.onDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("menuitem", { name: "حذف قطعی" }));

    await waitFor(() => expect(props.onDelete).toHaveBeenCalledWith("c1"));
  });

  it("keeps the row when the delete is cancelled", () => {
    const props = renderSidebar();

    fireEvent.click(screen.getByLabelText("گزینه‌های «بیمه‌نامه‌های من»"));
    fireEvent.click(screen.getByRole("menuitem", { name: "حذف" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "انصراف" }));

    expect(props.onDelete).not.toHaveBeenCalled();
  });
});
