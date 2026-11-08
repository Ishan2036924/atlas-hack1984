// The payments section's screens (lib/payments; the server is lifeos.api.payments): Settings › Payments lists every
// payment with where it stands; a row opens it in full (its steps, the taps that fit: Cancel a hold, Refund all or part
// with a reason, Report a problem, the evidence pack); limits lower at once and ask before going up.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { centsOf, money, paymentLine, raises } from "../../lib/payments";
import { ApiError } from "../../lib/net";
import { paymentsFakes } from "../../dev/paymentsPreview";
import { PaymentsSettings } from "./PaymentsSettings";

afterEach(cleanup);

const ok = <T,>(v: T) => Promise.resolve(v);

function show(params = "", over: Record<string, unknown> = {}) {
  const api = { ...paymentsFakes(new URLSearchParams(params), ok), ...over };
  const spied = Object.fromEntries(Object.entries(api).map(([k, f]) => [k, vi.fn(f as (...a: unknown[]) => unknown)]));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PaymentsSettings api={spied as never} />
    </QueryClientProvider>,
  );
  return spied;
}

describe("words and rules", () => {
  it("reads amounts exactly and says each state calmly", () => {
    expect(centsOf("12")).toBe(1200);
    expect(centsOf("$3.5")).toBe(350);
    expect(centsOf("0.07")).toBe(7);
    for (const bad of ["", "abc", "1.234", "-1", "1e3"]) expect(centsOf(bad)).toBeNull();
    expect(money(123456)).toBe("$1,234.56");
    const p = { amount_cents: 1200, payee: "Corner Café", refunded_cents: 300 };
    expect(paymentLine({ ...p, status: "partially_refunded" })).toBe("Paid $12.00 to Corner Café in test mode; $3.00 refunded.");
    expect(paymentLine({ ...p, status: "failed" })).toBe("The payment to Corner Café didn't go through. Nothing was paid.");
    expect(paymentLine({ ...p, status: "paid" })).toBe("Paid $12.00 to Corner Café in test mode.");
    // a payment made as a hold before 2026-11-07: no cancel anywhere, the server settles it
    expect(paymentLine({ ...p, status: "held" })).toBe("Holding $12.00 for Corner Café; it goes through shortly.");
    expect(raises({ per_payment_cents: 1000, per_day_cents: 2000, per_day_count: 2 }, { per_payment_cents: 500, per_day_cents: 2000, per_day_count: 3 })).toBe(true);
    expect(raises({ per_payment_cents: 1000, per_day_cents: 2000, per_day_count: 2 }, { per_payment_cents: 500, per_day_cents: 1000, per_day_count: 1 })).toBe(false);
  });
});

describe("Settings › Payments", () => {
  it("lists every payment with where it stands, newest first", async () => {
    show();
    const rows = await screen.findAllByRole("button", { name: /\$\d+\.\d\d, / });
    expect(rows).toHaveLength(6);
    expect(rows[0].getAttribute("aria-label")).toBe("Corner Café, $12.00, Paid");
    expect(screen.getByText("Partly refunded")).toBeTruthy();
    expect(screen.getByText("Didn't go through")).toBeTruthy();
  });

  it("says calmly when payments are off, and when there are none yet", async () => {
    show("payments=off");
    expect(await screen.findByText("Payments are off")).toBeTruthy();
    cleanup();
    show("payments=empty");
    expect(await screen.findByText(/No payments yet\./)).toBeTruthy();
  });

  it("a row opens the payment: paid at once, Refund and its steps, never a Cancel", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Corner Café, $12.00, Paid" }));
    expect(await screen.findByText("You approved it", {}, { timeout: 4000 })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Refund" })).toBeTruthy();
    const sheet = screen.getByRole("dialog");
    expect(within(sheet).queryByRole("button", { name: /cancel/i })).toBeNull();
    expect(within(sheet).queryByText(/cancel/i)).toBeNull();
  });

  it("Refund takes part of what is left, with a reason and one id for the tap", async () => {
    const api = show();
    fireEvent.click(await screen.findByRole("button", { name: "City Books, $24.00, Partly refunded" }));
    fireEvent.click(await screen.findByRole("button", { name: "Refund" }));
    const field = screen.getByLabelText("How much to refund") as HTMLInputElement;
    expect(field.value).toBe("15.00");
    fireEvent.change(field, { target: { value: "15.01" } });
    expect((screen.getByRole("button", { name: "Refund" }) as HTMLButtonElement).disabled).toBe(true); // more than is left
    fireEvent.change(field, { target: { value: "5" } });
    expect((screen.getByRole("button", { name: "Refund $5.00" }) as HTMLButtonElement).disabled).toBe(true); // a reason first
    expect(screen.getByText("Refunds can't be undone.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Paid twice" }));
    fireEvent.click(screen.getByRole("button", { name: "Refund $5.00" }));
    expect(await screen.findByText("Paid twice")).toBeTruthy();
    expect(api.refundPayment).toHaveBeenCalledWith("4a1b2c3d-0000-4000-8000-000000000002", { amount_cents: 500, reason: "duplicate", request: expect.stringMatching(/^[0-9a-f-]{36}$/) });
  });

  it("Report a problem adds a step and offers the evidence pack", async () => {
    const api = show();
    fireEvent.click(await screen.findByRole("button", { name: "Green Grocer, $6.50, Refunded" }));
    fireEvent.click(await screen.findByRole("button", { name: "Report a problem" }));
    const report = screen.getByRole("button", { name: "Report it" }) as HTMLButtonElement;
    expect(report.disabled).toBe(true); // a reason first
    fireEvent.click(screen.getByRole("button", { name: "Something else" }));
    fireEvent.change(screen.getByLabelText("Anything to add (optional)"), { target: { value: "wrong bag" } });
    fireEvent.click(screen.getByRole("button", { name: "Report it" }));
    expect(await screen.findByText(/Added to this payment's steps\./)).toBeTruthy();
    expect(api.reportPayment).toHaveBeenCalledWith("4a1b2c3d-0000-4000-8000-000000000004", { reason: "other", note: "wrong bag" });
    expect(screen.getAllByRole("button", { name: "Download evidence pack" }).length).toBeGreaterThan(0);
  });

  it("a lower limit saves at once; a higher one asks first", async () => {
    const api = show();
    fireEvent.click(await screen.findByRole("button", { name: /^Limits/ }));
    const one = await screen.findByRole("radiogroup", { name: "Most for one payment" });
    fireEvent.click(within(one).getByText("$10"));
    await vi.waitFor(() => expect(api.setPaymentLimits).toHaveBeenLastCalledWith({ per_payment_cents: 1000, per_day_cents: 5000, per_day_count: 5, test_card: "visa" }));
    expect(screen.queryByText("Raise your limit?")).toBeNull();
    fireEvent.click(within(one).getByText("$50"));
    expect(await screen.findByRole("region", { name: "Raise your limit?" })).toBeTruthy();
    expect(api.setPaymentLimits).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Raise it" }));
    await vi.waitFor(() => expect(api.setPaymentLimits).toHaveBeenLastCalledWith({ per_payment_cents: 5000, per_day_cents: 5000, per_day_count: 5, test_card: "visa", confirm_raise: true }));
  });

  it("a load that fails says so with Try again", async () => {
    show("", { payments: () => Promise.reject(new ApiError(503, "down")) });
    expect(await screen.findByText("Payments couldn't load")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("a disputed payment says what it means and offers no refund", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Print Shop, $18.00, Disputed" }));
    expect(await screen.findByText(/You can't refund it while that's open\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Refund" })).toBeNull();
    expect(screen.getByText(/The bank says the card's owner didn't make this payment\./)).toBeTruthy();
  });

  it("the test card is one quiet row; its sheet switches the next payment's card", async () => {
    const api = show();
    expect(await screen.findByText("Normal")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Test card/ }));
    fireEvent.click(within(await screen.findByRole("radiogroup", { name: "Test card for the next payment" })).getByText("Gets disputed"));
    await vi.waitFor(() => expect(api.setPaymentLimits).toHaveBeenLastCalledWith({ per_payment_cents: 2000, per_day_cents: 5000, per_day_count: 5, test_card: "dispute" }));
  });

  it("the chat's line opens the section on its own payment", async () => {
    const api = paymentsFakes(new URLSearchParams(""), ok);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <PaymentsSettings api={api as never} initialPayment="3f2b8c1e-5d4a-4c3b-9a2e-1f0e9d8c7b6a" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("You approved it", {}, { timeout: 4000 })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Refund" })).toBeTruthy();
  });
});
