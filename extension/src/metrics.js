(() => {
  function finiteNumbers(values) {
    return values.filter(Number.isFinite);
  }

  function percentile(values, ratio) {
    const sorted = finiteNumbers(values).sort((a, b) => a - b);
    if (sorted.length === 0) return null;
    const index = (sorted.length - 1) * ratio;
    const lower = Math.floor(index);
    const upper = Math.ceil(index);
    if (lower === upper) return sorted[lower];
    return sorted[lower] + (sorted[upper] - sorted[lower]) * (index - lower);
  }

  function summarize(samples, targetDelay) {
    const valid = samples.filter((sample) => Number.isFinite(sample.actualDelay));
    const delays = valid.map((sample) => sample.actualDelay);
    const stable = valid.filter((sample) => Math.abs(sample.actualDelay - targetDelay) <= 1);
    const absoluteErrors = valid.map((sample) => Math.abs(sample.actualDelay - targetDelay));

    let longestStableSeconds = 0;
    let currentStableSeconds = 0;
    for (const sample of valid) {
      if (Math.abs(sample.actualDelay - targetDelay) <= 1) {
        currentStableSeconds += 1;
        longestStableSeconds = Math.max(longestStableSeconds, currentStableSeconds);
      } else {
        currentStableSeconds = 0;
      }
    }

    return {
      sampleCount: samples.length,
      validDelaySampleCount: valid.length,
      actualDelayP50: percentile(delays, 0.5),
      actualDelayP95: percentile(delays, 0.95),
      meanAbsoluteError: absoluteErrors.length
        ? absoluteErrors.reduce((sum, value) => sum + value, 0) / absoluteErrors.length
        : null,
      targetBandRatio: valid.length ? stable.length / valid.length : null,
      longestStableSeconds
    };
  }

  function csvEscape(value) {
    if (value == null) return "";
    const text = typeof value === "object" ? JSON.stringify(value) : String(value);
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  }

  function samplesToCsv(samples) {
    if (samples.length === 0) return "";
    const keys = [...new Set(samples.flatMap((sample) => Object.keys(sample)))];
    const rows = [keys.join(",")];
    for (const sample of samples) {
      rows.push(keys.map((key) => csvEscape(sample[key])).join(","));
    }
    return rows.join("\n");
  }

  window.LiveDelaySpike = window.LiveDelaySpike || {};
  window.LiveDelaySpike.metrics = { percentile, summarize, samplesToCsv };
})();
