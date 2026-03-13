export interface ModelUsage {
  model: string;
  inputUsage: number;
  outputUsage: number;
  totalUsage: number;
  totalCost: number;
  countObservations: number;
  countTraces: number;
}

export interface StatisticsData {
  date: string;
  countTraces: number;
  countObservations: number;
  totalCost: number;
  usage: ModelUsage[];
}

export interface StatisticsResponse {
  data: StatisticsData[];
} 