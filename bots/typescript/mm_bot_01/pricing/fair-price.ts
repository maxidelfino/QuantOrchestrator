// Fair Price Provider interface (Dependency Inversion)
export interface FairPriceProvider {
  addSample(localMid: number, referenceMid: number): void;
  getFairPrice(referenceMid: number): number | null;
  getMedianOffset(): number | null;
  getRawMedianOffset(): number | null;
  getSampleCount(): number;
  getState(): { offset: number | null; samples: number };
}

const MAX_SAMPLES = 500;

export interface FairPriceConfig {
  readonly windowMs: number;
  readonly minSamples: number;
}

interface OffsetSample {
  offset: number;
  second: number;
}

// fair_price = reference_mid + median(local_mid - reference_mid)
// Uses Binance as reference and 01 Exchange as local.
export class FairPriceCalculator implements FairPriceProvider {
  private samples: OffsetSample[] = [];
  private head = 0;
  private count = 0;
  private lastSecond = 0;

  constructor(private readonly config: FairPriceConfig) {}

  addSample(localMid: number, referenceMid: number): void {
    const now = Date.now();
    const currentSecond = Math.floor(now / 1000);
    if (currentSecond <= this.lastSecond) return;
    this.lastSecond = currentSecond;

    const offset = localMid - referenceMid;
    this.samples[this.head] = { offset, second: currentSecond };
    this.head = (this.head + 1) % MAX_SAMPLES;
    if (this.count < MAX_SAMPLES) this.count++;
  }

  private getValidSamples(): OffsetSample[] {
    const cutoffSecond = Math.floor((Date.now() - this.config.windowMs) / 1000);
    const valid: OffsetSample[] = [];
    for (let i = 0; i < this.count; i++) {
      const sample = this.samples[i];
      if (sample && sample.second > cutoffSecond) valid.push(sample);
    }
    return valid;
  }

  getMedianOffset(): number | null {
    const valid = this.getValidSamples();
    if (valid.length < this.config.minSamples) return null;
    const offsets = valid.map((s) => s.offset).sort((a, b) => a - b);
    const mid = Math.floor(offsets.length / 2);
    return offsets.length % 2 === 0
      ? (offsets[mid - 1] + offsets[mid]) / 2
      : offsets[mid];
  }

  getFairPrice(referenceMid: number): number | null {
    const offset = this.getMedianOffset();
    if (offset === null) return null;
    return referenceMid + offset;
  }

  getSampleCount(): number {
    return this.getValidSamples().length;
  }

  getRawMedianOffset(): number | null {
    const valid = this.getValidSamples();
    if (valid.length === 0) return null;
    const offsets = valid.map((s) => s.offset).sort((a, b) => a - b);
    const mid = Math.floor(offsets.length / 2);
    return offsets.length % 2 === 0
      ? (offsets[mid - 1] + offsets[mid]) / 2
      : offsets[mid];
  }

  getState(): { offset: number | null; samples: number } {
    return {
      offset: this.getRawMedianOffset(),
      samples: this.getSampleCount(),
    };
  }
}
