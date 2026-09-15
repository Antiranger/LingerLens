/* LingerLens language catalog + accessible searchable language selector.
 *
 * The catalog (canonical BCP 47 tag, English name, autonym, direction,
 * aliases) comes from the loopback /api/languages endpoint and is identity
 * data only -- Provider support is decided by the capability payload.
 *
 * Pure helpers (setCatalog/filterLanguages/directionFor/nameParts) never
 * touch the DOM so they can be unit-tested under Node's vm. The combobox
 * follows the WAI-ARIA 1.2 pattern: role=combobox input + role=listbox
 * popup, ArrowUp/Down/Home/End to move, Enter to commit, Escape to cancel.
 */
(() => {
  "use strict";

  let catalog = [];
  const byTag = new Map();
  // Quick-access order for the empty-query list.
  const COMMON_TAGS = ["ja", "zh-Hans", "zh-Hant", "en", "ko"];

  function setCatalog(entries) {
    catalog = Array.isArray(entries) ? entries.slice() : [];
    byTag.clear();
    for (const entry of catalog) byTag.set(entry.tag, entry);
  }

  function entries() {
    return catalog.slice();
  }

  function entry(tag) {
    return byTag.get(tag) || null;
  }

  function normalize(text) {
    return String(text || "")
      .toLocaleLowerCase()
      .normalize("NFD")
      .replace(/\p{Diacritic}/gu, "");
  }

  /* Display names may be localized with Intl.DisplayNames for the UI locale;
   * server validation stays authoritative and catalog-independent. */
  let displayNames = null;
  function localizedName(tag, locale) {
    try {
      if (typeof Intl !== "undefined" && Intl.DisplayNames) {
        displayNames = displayNames || new Intl.DisplayNames([locale || "zh-CN"], { type: "language" });
        const name = displayNames.of(tag);
        if (name && name !== tag) return name;
      }
    } catch (_) { /* older browser: catalog English name */ }
    const found = entry(tag);
    return found ? found.englishName : tag;
  }

  function nameParts(tag, locale) {
    const found = entry(tag);
    return {
      primary: localizedName(tag, locale),
      autonym: found ? found.autonym : tag,
      tag,
      direction: directionFor(tag),
    };
  }

  const RTL_PRIMARY = new Set(["ar", "he", "fa", "ur", "ps", "sd", "ug", "dv", "ks", "yi", "ckb", "ku"]);
  function directionFor(tag) {
    const found = entry(tag);
    if (found && found.direction) return found.direction;
    // Catalog fallback for tags outside it (and browsers without dir=auto):
    // the primary subtag decides.
    const primary = String(tag || "").split("-")[0];
    return RTL_PRIMARY.has(primary) ? "rtl" : "ltr";
  }

  /* Search by localized name, English name, autonym, tag and aliases. */
  function filterLanguages(query, locale, limit = null) {
    const resultLimit = Number.isFinite(limit) ? Math.max(0, Number(limit)) : catalog.length;
    const needle = normalize(query).trim();
    if (!needle) {
      const common = COMMON_TAGS.filter((tag) => byTag.has(tag));
      const rest = catalog.map((item) => item.tag).filter((tag) => !common.includes(tag));
      return common.concat(rest).slice(0, resultLimit);
    }
    const scored = [];
    for (const item of catalog) {
      const haystacks = [
        item.tag,
        item.englishName,
        item.autonym,
        localizedName(item.tag, locale),
        ...(item.aliases || []),
      ].map(normalize);
      let score = -1;
      for (const hay of haystacks) {
        if (hay === needle) { score = Math.max(score, 3); }
        else if (hay.startsWith(needle)) { score = Math.max(score, 2); }
        else if (hay.includes(needle)) { score = Math.max(score, 1); }
      }
      if (score >= 0) scored.push([score, item.tag]);
    }
    scored.sort((a, b) => b[0] - a[0] || a[1].localeCompare(b[1]));
    return scored.slice(0, resultLimit).map(([, tag]) => tag);
  }

  let comboCounter = 0;

  /* A keyboard-accessible single-select language combobox.
   * options: { value, placeholder, locale, onChange(tag), disabled } */
  function createLanguageSelector(host, options) {
    const settings = options || {};
    const locale = settings.locale || (typeof document !== "undefined" ? document.documentElement.lang : "zh-CN");
    comboCounter += 1;
    const listId = `lang-listbox-${comboCounter}`;
    const fallback = host.querySelector(".language-native-fallback");
    const fallbackValue = fallback?.value || "";
    host.classList.add("lang-combo");
    host.innerHTML = "";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "lang-combo-input";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", listId);
    input.setAttribute("aria-autocomplete", "list");
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = settings.placeholder || "搜索并选择语言…";
    input.title = "点击展开语言列表，也可以直接输入名称或 BCP 47 tag 搜索";
    const list = document.createElement("ul");
    list.className = "lang-combo-list";
    list.id = listId;
    list.setAttribute("role", "listbox");
    list.hidden = true;
    host.append(input, list);

    let value = "";
    let matches = [];
    let activeIndex = -1;

    function commit(tag, notify = true) {
      value = tag;
      const parts = nameParts(tag, locale);
      input.value = tag ? `${parts.primary} · ${parts.autonym} · ${parts.tag}` : "";
      close();
      if (notify && typeof settings.onChange === "function") settings.onChange(tag);
    }

    function open({ showAll = false } = {}) {
      if (showAll) input.value = "";
      render();
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      activeIndex = -1;
    }

    function render() {
      matches = filterLanguages(input.value, locale);
      list.innerHTML = "";
      matches.forEach((tag, index) => {
        const parts = nameParts(tag, locale);
        const option = document.createElement("li");
        option.id = `${listId}-opt-${index}`;
        option.setAttribute("role", "option");
        option.dataset.tag = tag;
        option.dir = "auto";
        option.className = "lang-combo-option";
        const primary = document.createElement("b");
        primary.textContent = parts.primary;
        const secondary = document.createElement("small");
        secondary.textContent = `${parts.autonym} · ${parts.tag}`;
        option.append(primary, secondary);
        if (tag === value) option.setAttribute("aria-selected", "true");
        if (index === activeIndex) option.classList.add("is-active");
        option.addEventListener("pointerdown", (event) => {
          event.preventDefault(); // keep focus on the input
          commit(tag);
        });
        list.append(option);
      });
      if (!matches.length) {
        const empty = document.createElement("li");
        empty.className = "lang-combo-empty";
        empty.textContent = "没有匹配的语言";
        list.append(empty);
      }
    }

    function move(delta) {
      if (list.hidden) { open(); return; }
      if (!matches.length) return;
      activeIndex = (activeIndex + delta + matches.length) % matches.length;
      input.setAttribute("aria-activedescendant", `${listId}-opt-${activeIndex}`);
      render();
      const active = list.querySelector(".is-active");
      if (active) active.scrollIntoView({ block: "nearest" });
    }

    input.addEventListener("focus", () => {
      // Opening a selector shows the complete catalog. The committed display
      // label is not a search query; treating "日语 · 日本語 · ja" as one
      // query produced an empty list. Typing starts a real search from blank.
      activeIndex = -1;
      open({ showAll: true });
    });
    input.addEventListener("pointerdown", () => {
      // When Escape closed the popup without moving focus, a second click must
      // still reopen the complete list.
      if (document.activeElement === input && list.hidden) open({ showAll: true });
    });
    input.addEventListener("input", () => { activeIndex = -1; open(); });
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") { event.preventDefault(); move(1); }
      else if (event.key === "ArrowUp") { event.preventDefault(); move(-1); }
      else if (event.key === "Home" && !list.hidden) { event.preventDefault(); activeIndex = -1; move(1); }
      else if (event.key === "End" && !list.hidden) { event.preventDefault(); activeIndex = 0; move(-1); }
      else if (event.key === "Enter") {
        if (!list.hidden && activeIndex >= 0 && matches[activeIndex]) {
          event.preventDefault();
          commit(matches[activeIndex]);
        } else if (!list.hidden && matches.length) {
          event.preventDefault();
          commit(matches[0]);
        }
      } else if (event.key === "Escape") {
        close();
        commit(value, false); // restore the committed label
      } else if (event.key === "Tab") {
        close();
      }
    });
    input.addEventListener("blur", () => {
      // Restore the committed label when the user leaves without selecting.
      setTimeout(() => { if (document.activeElement !== input) commit(value, false); }, 0);
    });

    if (settings.disabled) input.disabled = true;
    commit(settings.value || fallbackValue || "", false);

    return {
      get value() { return value; },
      set value(tag) { commit(tag || "", false); },
      focus: () => input.focus(),
      setDisabled(disabled) { input.disabled = !!disabled; },
      host,
      input,
    };
  }

  const api = {
    setCatalog,
    entries,
    entry,
    filterLanguages,
    nameParts,
    directionFor,
    createLanguageSelector,
  };
  if (typeof window !== "undefined") window.LingerLensLanguages = api;
  if (typeof globalThis !== "undefined") globalThis.LingerLensLanguages = api;
})();
