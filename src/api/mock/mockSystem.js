export const mockSystemInference = {
  phase: 'inference',
  notes: 'Training completed and promoted (snapshot: 2024-06-24T02-10-45Z).',
  models_version: 3,
  last_retrain_at: Date.now() / 1000 - 86400 * 2,
  last_training_result: 'passed',
  observation: {
    ready: false,
    days_elapsed: 16.2,
    days_required: 14,
    netflow_records: 145000,
    records_required: 100000,
  },
}

export const mockSystemObservation = {
  phase: 'observation',
  notes: 'Collecting initial training data.',
  models_version: 0,
  last_retrain_at: null,
  last_training_result: null,
  observation: {
    ready: false,
    days_elapsed: 3.5,
    days_required: 14,
    netflow_records: 42150,
    records_required: 100000,
  },
}

// swap these to test different phases
export const mockSystemStatus = mockSystemInference
