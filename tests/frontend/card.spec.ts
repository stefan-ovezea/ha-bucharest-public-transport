import {test, expect} from "@playwright/test";

test.beforeEach(async ({page}) => {
  await page.route("https://tile.openstreetmap.org/**", route => route.fulfill({contentType:"image/png",body:Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=","base64")}));
});

test("renders arrivals, map, provenance, alerts and observation time", async ({page}) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", {name:"Piața Unirii"})).toBeVisible();
  await expect(page.locator(".arrival")).toHaveCount(3);
  await expect(page.locator(".source.timetable")).toHaveText("Timetable");
  await expect(page.locator(".vehicle-badge")).toHaveText("41");
  await page.locator(".vehicle-marker").click();
  await expect(page.locator(".leaflet-popup-content")).toContainText("Travelling to Piața Unirii");
  await expect(page.locator(".leaflet-popup-content")).toContainText("Observed");
  await page.getByText("Service alerts (1)").click();
  await expect(page.getByText("Temporary platform change")).toBeVisible();
  expect(errors).toEqual([]);
});

test("fuzzy search keeps ambiguous platforms separate and filters lines", async ({page}) => {
  await page.goto("/");
  await page.getByRole("searchbox").fill("Pta Uniri");
  await expect(page.locator(".results button")).toHaveCount(2);
  await page.locator(".results button").nth(1).click();
  await expect.poll(()=>page.evaluate(()=>window.commands.filter(c=>c.type.endsWith("subscribe_board")).at(-1).stop_id)).toBe("U2");
  await page.locator(".line-label select").selectOption("M1");
  await expect.poll(()=>page.evaluate(()=>window.commands.filter(c=>c.type.endsWith("subscribe_vehicles")).at(-1).route_ids)).toEqual(["M1"]);
  await expect.poll(()=>page.evaluate(()=>window.activeSubscriptions)).toBe(2);
});

test("Romanian UI and stale fallback", async ({page}) => {
  await page.goto("/?lang=ro&stale=1");
  await expect(page.getByRole("searchbox")).toHaveAttribute("placeholder","Nume complet, parțial sau cu mici greșeli");
  await expect(page.locator(".notice")).toContainText("Predicțiile în timp real sunt vechi");
  await expect(page.locator(".source.timetable")).toHaveText("Orar");
});

test("missing metro times stay unavailable", async ({page}) => {
  await page.goto("/?state=times_unavailable");
  await expect(page.locator(".empty-state")).toHaveText("Published times are unavailable for this station.");
  await expect(page.locator(".arrival")).toHaveCount(0);
});

test("an interrupted connection cannot leave old vehicles or predictions labelled live", async ({page}) => {
  await page.clock.install();
  await page.goto("/");
  await expect(page.locator(".vehicle-badge")).toHaveCount(1);
  await page.clock.fastForward(92000);
  await expect(page.locator(".vehicle-badge")).toHaveCount(0);
  await expect(page.locator(".source.stale")).toHaveCount(2);
  await expect(page.locator(".notice")).toContainText("stale");
});

test("offline state is useful and map tile failures preserve arrivals", async ({page}) => {
  await page.goto("/?error=1");
  await expect(page.locator("[role=status]")).toContainText("unavailable");
  await page.unroute("https://tile.openstreetmap.org/**");
  await page.route("https://tile.openstreetmap.org/**", route=>route.abort());
  await page.goto("/");
  await expect(page.locator(".map-notice")).toContainText("Map tiles are unavailable");
  await expect(page.locator(".arrival")).toHaveCount(3);
});

test("unsubscribes on disconnect and reconnects without leaks", async ({page}) => {
  await page.goto("/");
  await expect.poll(()=>page.evaluate(()=>window.activeSubscriptions)).toBe(2);
  await page.evaluate(()=>window.card.remove());
  await expect.poll(()=>page.evaluate(()=>window.activeSubscriptions)).toBe(0);
  await page.evaluate(()=>document.querySelector("main").append(window.card));
  await expect.poll(()=>page.evaluate(()=>window.activeSubscriptions)).toBe(2);
});

test("visual editor emits config changes and map can be hidden", async ({page}) => {
  await page.goto("/?no_map=1");
  await expect(page.locator(".map-wrap")).toBeHidden();
  await page.evaluate(async()=>{const editor = await window.card.constructor.getConfigElement();editor.setConfig({type:"custom:bucharest-transport-card"});editor.hass=window.hass;editor.addEventListener("config-changed",e=>window.changedConfig=e.detail.config);document.querySelector("main").append(editor);});
  const title = page.locator("bucharest-transport-card-editor input").first();
  await title.fill("My commute");await title.blur();
  await expect.poll(()=>page.evaluate(()=>window.changedConfig?.title)).toBe("My commute");
});

test("metro estimates are labelled in both languages and inherit the saved board option", async ({page}) => {
  for (const [lang, label] of [["en", "Estimated"], ["ro", "Estimat"]]) {
    await page.goto(`/?state=estimated&lang=${lang}`);
    await expect(page.locator(".source.estimated")).toHaveCount(3);
    await expect(page.locator(".status")).toHaveText(label);
    await expect(page.locator(".estimate-notice")).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.commands.filter(c => c.type.endsWith("subscribe_board")).at(-1).estimate_missing_times)).toBe(true);
    await expect(page.locator(".source.live")).toHaveCount(0);
  }
});

test("editor enables estimates for a transient board", async ({page}) => {
  await page.goto("/?no_map=1");
  await page.evaluate(async () => {
    const editor = await window.card.constructor.getConfigElement();
    editor.setConfig({type: "custom:bucharest-transport-card"});
    editor.hass = window.hass;
    editor.addEventListener("config-changed", e => window.changedConfig = e.detail.config);
    document.querySelector("main").append(editor);
  });
  await page.getByLabel("Estimate missing metro times", {exact: true}).check();
  await expect.poll(() => page.evaluate(() => window.changedConfig?.estimate_missing_times)).toBe(true);
  await page.evaluate(() => window.card.setConfig({...window.changedConfig, stop_id: "U"}));
  await expect.poll(() => page.evaluate(() => window.commands.filter(c => c.type.endsWith("subscribe_board")).at(-1).estimate_missing_times)).toBe(true);
});

test("reloading module URLs leaves one picker entry without redefining elements", async ({page}) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await page.evaluate(async () => {
    window.customCards.push({type: "bucharest-transport-card"}, {type: "custom:bucharest-transport-card"}, {type: "other-card"});
    await import("/card.js?duplicate=1");
    await import("/card.js?duplicate=2");
  });
  expect(await page.evaluate(() => window.customCards.filter(c => c.type?.replace(/^custom:/, "") === "bucharest-transport-card").length)).toBe(1);
  expect(await page.evaluate(() => window.customCards.some(c => c.type === "other-card"))).toBe(true);
  await expect(page.locator(".arrival")).toHaveCount(3);
  expect(errors).toEqual([]);
});

test("Academia search clears surface filters and offers labelled estimates in the card", async ({page}) => {
  await page.goto("/?surface_filter=1");
  await page.getByRole("searchbox").fill("Academia Militara");
  await page.locator(".results button").click();
  await expect(page.getByRole("heading", {name: "Academia militară"})).toBeVisible();
  await expect(page.locator(".empty-state")).toContainText("Published times are unavailable");
  await expect.poll(() => page.evaluate(() => window.commands.filter(c => c.type.endsWith("subscribe_board")).at(-1).route_ids)).toEqual([]);
  await page.getByLabel("Estimate missing metro times", {exact:true}).check();
  await expect(page.locator(".source.estimated")).toHaveText("Estimated");
  await expect(page.locator(".arrival")).toContainText("M5");
  await expect(page.locator(".arrival")).toContainText("Eroilor");
  await expect(page.locator(".estimate-notice")).toBeVisible();
  // Reconnect without losing the exploratory station, filters, or estimate choice.
  await page.evaluate(() => {
    const hass = {...window.hass, connection: {...window.hass.connection}};
    window.card.hass = hass;
  });
  await expect.poll(() => page.evaluate(() => window.commands.filter(c => c.type.endsWith("subscribe_board")).at(-1).stop_id)).toBe("9909R");
  await expect(page.getByLabel("Estimate missing metro times", {exact:true})).toBeChecked();
  await expect(page.locator(".source.estimated")).toHaveCount(1);
});
