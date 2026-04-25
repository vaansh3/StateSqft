export type TimeSeriesChartSpec = {
  kind: "line";
  title: string;
  subtitle?: string;
  x_labels: string[];
  values: (number | null)[];
  y_axis_label?: string;
  mean_reference?: number | null;
};

export type PieChartSpec = {
  kind: "pie";
  title: string;
  subtitle?: string;
  categories: string[];
  values: (number | null)[];
};

export type ChartSpec = TimeSeriesChartSpec | PieChartSpec;

export type VerdictPayload = {
  answer: "yes" | "no" | "unclear";
  headline: string;
  reason: string;
  basis: string[];
};

export type MetroViz = {
  region_id: number;
  region_name: string;
  region_type: string;
  size_rank: number;
  period_label: string;
  timing_as_of?: string | null;
  selling_insights?: string[];
  metric_mean?: number | null;
  metric_min?: number | null;
  metric_max?: number | null;
  metric_latest?: number | null;
  metric_avg_6m?: number | null;
  metric_vs_avg_6m_pct?: number | null;
  charts: ChartSpec[];
  verdict?: VerdictPayload | null;
};

export type ChatStructuredBlock = {
  title?: string;
  summary?: string;
  key_points?: string[];
  caveats?: string[];
};

export type ChatApiResponse = {
  reply: string;
  structured?: ChatStructuredBlock;
  dataset_note?: string;
  data_window?: string;
  metros?: MetroViz[];
};
