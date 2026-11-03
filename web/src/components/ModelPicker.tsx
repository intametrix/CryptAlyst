import { memo, useMemo, useState } from 'react';

export interface PickableModel {
  name: string;
  family: string;
  lookback: string;
  params: string;
  output: string;
  describedAs: string;
}

export interface PickedScore {
  model: string;
  mape: number;
  coverage80: number;
}

/**
 * Choosing which model to train.
 *
 * The list runs to fifty entries once the catalogue is served, so it is a filter, a
 * grouped table and nothing else. Its own filter state lives here: the panel had no
 * business owning a search box.
 */
export const ModelPicker = memo(function ModelPicker({
  models,
  scores,
  best,
  chosen,
  onPick,
}: {
  models: PickableModel[];
  scores: PickedScore[];
  best: string | null | undefined;
  chosen: string;
  onPick: (name: string) => void;
}) {
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q === '') return models;
    return models.filter((m) => m.name.toLowerCase().includes(q) || m.family.toLowerCase().includes(q));
  }, [models, query]);

  const groups = useMemo(() => {
    const byFamily = new Map<string, PickableModel[]>();
    for (const m of filtered) {
      const list = byFamily.get(m.family);
      if (list === undefined) byFamily.set(m.family, [m]);
      else list.push(m);
    }
    return [...byFamily.entries()]
      .map(([family, items]) => ({ family, items }))
      .sort((a, b) => a.family.localeCompare(b.family));
  }, [filtered]);

  const scoreOf = (name: string) => scores.find((s) => s.model === name);

  return (
    <>
      <div className="fl-model-tools">
        <input
          className="fl-model-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="filter models by name or family"
          aria-label="filter models"
        />
        <span className="fl-model-count">{filtered.length} of {models.length}</span>
      </div>

      <div className="fl-model-row fl-model-row-head" role="presentation">
        <span>model</span><span>lookback</span><span>params</span><span>output</span><span>MAPE</span><span>coverage</span>
      </div>

      <div className="fl-models">
        {groups.map((g) => (
          <section className="fl-model-group" key={g.family}>
            <h4 className="fl-model-group-head">
              {g.family} <span>{g.items.length}</span>
            </h4>
            <div className="fl-model-rows" role="list">
              {g.items.map((m) => {
                const row = scoreOf(m.name);
                const isChosen = chosen === m.name;
                return (
                  <button
                    type="button"
                    key={m.name}
                    role="listitem"
                    className={`fl-model-row${isChosen ? ' is-chosen' : ''}`}
                    aria-pressed={isChosen}
                    title={m.describedAs}
                    onClick={() => onPick(m.name)}
                  >
                    <span className="fl-model-row-name">
                      {m.name}
                      {best === m.name && <span className="fl-model-best">best</span>}
                      {isChosen && <span className="fl-model-chosen">selected</span>}
                    </span>
                    <span>{m.lookback}</span>
                    <span>{m.params}</span>
                    <span>{m.output}</span>
                    <span className="num fl-model-row-mape">{row !== undefined ? `${row.mape.toFixed(3)}%` : '—'}</span>
                    <span className="num">{row !== undefined ? `${(row.coverage80 * 100).toFixed(0)}%` : '—'}</span>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
        {groups.length === 0 && <p className="fl-hint">no model matches “{query}”</p>}
      </div>
    </>
  );
});
