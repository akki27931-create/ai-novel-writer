/** 设置页：DeepSeek 连接、模型路由、生成参数、向量、番茄 MCP、提示词。 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { toast } from '../components/Toast'
import type {
  AppSettings,
  FanqieSelftest,
  FanqieStatus,
  ModelInfo,
  ModelValidation,
  PromptItem,
  SystemInfo,
} from '../types'

interface FormState {
  base_url: string
  text_model: string
  reasoning_model: string
  temperature: number
  top_p: number
  max_tokens: number
  retrieval_top_k: number
  embedding_model: string
  usd_to_cny: number
  fanqie_mcp_command: string
  fanqie_api_base: string
}

const EMPTY_FORM: FormState = {
  base_url: 'https://api.deepseek.com',
  text_model: '',
  reasoning_model: '',
  temperature: 0.85,
  top_p: 0.95,
  max_tokens: 8192,
  retrieval_top_k: 8,
  embedding_model: 'BAAI/bge-small-zh-v1.5',
  usd_to_cny: 7.2,
  fanqie_mcp_command: '',
  fanqie_api_base: '',
}

export default function Settings() {
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [raw, setRaw] = useState<AppSettings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [pricingText, setPricingText] = useState('{}')
  const [candidates, setCandidates] = useState<string[]>([])
  const [models, setModels] = useState<ModelInfo[]>([])
  const [validation, setValidation] = useState<ModelValidation | null>(null)
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)
  const [prompts, setPrompts] = useState<PromptItem[]>([])
  const [fanqie, setFanqie] = useState<FanqieStatus | null>(null)
  const [selftest, setSelftest] = useState<FanqieSelftest | null>(null)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loadingModels, setLoadingModels] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([api.getSettings(), api.listPrompts()])
      const settings = s.settings
      setRaw(settings)
      setCandidates(s.candidate_models)
      setForm({
        base_url: settings.base_url,
        text_model: settings.text_model,
        reasoning_model: settings.reasoning_model,
        temperature: settings.temperature,
        top_p: settings.top_p,
        max_tokens: settings.max_tokens,
        retrieval_top_k: settings.retrieval_top_k,
        embedding_model: settings.embedding_model,
        usd_to_cny: settings.usd_to_cny,
        fanqie_mcp_command: settings.fanqie_mcp_command,
        fanqie_api_base: settings.fanqie_api_base || '',
      })
      setPricingText(JSON.stringify(settings.pricing, null, 2))
      setPrompts(p.items)

      // 顺便校验模型名（/models 是免费接口）：配置错就直接在页面上报警，避免用户白跑一次拆书
      try {
        setValidation(await api.validateModels())
      } catch {
        setValidation(null)
      }

      // 显示当前真正在用的向量后端，方便确认语义向量有没有生效
      try {
        setSystemInfo(await api.systemInfo())
      } catch {
        setSystemInfo(null)
      }
    } catch (error) {
      toast.error((error as Error).message)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const save = async () => {
    setSaving(true)
    try {
      let pricing: AppSettings['pricing'] | undefined
      try {
        pricing = JSON.parse(pricingText)
      } catch (error) {
        toast.error(`单价 JSON 格式错误：${(error as Error).message}`)
        setSaving(false)
        return
      }

      const body: Record<string, unknown> = { ...form, pricing }
      if (apiKey.trim()) body.api_key = apiKey.trim()

      await api.updateSettings(body)
      setApiKey('')
      toast.success('设置已保存')
      load()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const clearApiKey = async () => {
    if (!window.confirm('确定清除已保存的 API Key？（环境变量里的不受影响）')) return
    try {
      await api.updateSettings({ api_key: null })
      toast.success('已清除数据库中的 API Key')
      load()
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  const fetchModels = async () => {
    setLoadingModels(true)
    try {
      const list = await api.listModels()
      setModels(list)
      const result = await api.validateModels()
      setValidation(result)
      if (result.ok) {
        toast.success(`获取到 ${list.length} 个可用模型，当前模型名配置正确`)
      } else {
        toast.error('检测到模型名配置有误，请看下方红色提示')
      }
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setLoadingModels(false)
    }
  }

  /** 一键把不存在的模型名改成账号真实可用的模型。 */
  const autofixModels = async () => {
    try {
      const result = await api.autofixModels()
      const applied = Object.entries(result.applied)
      if (applied.length === 0) {
        toast.info('无需修正')
      } else {
        toast.success(`已修正：${applied.map(([k, v]) => `${k}=${v}`).join('，')}`)
      }
      load()
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  const testFanqie = async () => {
    try {
      setFanqie(await api.fanqieStatus())
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  /** 完整自检：先保存设置（否则后端读到的还是旧命令），再逐步检查整条链路。 */
  const runSelftest = async () => {
    setTesting(true)
    setSelftest(null)
    try {
      await save()
      const result = await api.fanqieSelftest()
      setSelftest(result)
      if (result.ok) {
        toast.success('自检通过，番茄链路可用')
      } else {
        toast.error('自检未通过，请看下方的步骤详情')
      }
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setTesting(false)
    }
  }

  const sourceLabel =
    raw?.api_key_source === 'env' ? '环境变量' : raw?.api_key_source === 'db' ? '本地数据库' : '未配置'

  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">设置</h2>
          <p className="mt-1 text-sm text-slate-500">所有配置保存在本地，API Key 不会上传到第三方。</p>
        </div>
        <button className="btn-primary" onClick={save} disabled={saving}>
          {saving ? '保存中…' : '保存设置'}
        </button>
      </header>

      {/* ---------- DeepSeek ---------- */}
      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-slate-300">DeepSeek 连接</h3>
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="label">
              API Key
              <span className="ml-2 text-slate-500">
                {raw?.api_key_present ? `已配置（来源：${sourceLabel}）` : '未配置'}
              </span>
            </label>
            <input
              type="password"
              className="input"
              placeholder={raw?.api_key_present ? '留空表示不修改' : 'sk-...'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
            <div className="mt-1 flex gap-2">
              <button className="btn-ghost !px-2 !py-1 text-xs" onClick={clearApiKey}>
                清除已保存的 Key
              </button>
              <span className="text-[11px] text-slate-500">
                推荐直接用环境变量 DEEPSEEK_API_KEY（优先级更高）
              </span>
            </div>
          </div>
          <div>
            <label className="label">Base URL</label>
            <input className="input" value={form.base_url} onChange={(e) => update('base_url', e.target.value)} />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-ghost" onClick={fetchModels} disabled={loadingModels}>
            {loadingModels ? '拉取中…' : '拉取可用模型'}
          </button>
          {models.map((model) => (
            <button
              key={model.id}
              className="tag border border-slate-700 bg-slate-800 text-slate-300 hover:border-indigo-500"
              onClick={() => update('text_model', model.id)}
              title="点击填入「正文模型」"
            >
              {model.id}
            </button>
          ))}
        </div>
        <p className="text-[11px] text-slate-500">
          点击上面的模型名只会填入「正文模型」。复杂任务模型请手动填，或到下面用「一键修正」。
        </p>
      </section>

      {/* ---------- 模型路由 ---------- */}
      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-slate-300">模型路由</h3>
        <p className="rounded-lg border border-slate-800 bg-slate-950/50 p-2 text-xs text-slate-400">
          正文续写 / 扩写 / 润色 / 摘要 → <b>正文模型</b>；拆书 / 大纲推演 / 伏笔检查 / 逻辑矛盾修复 →{' '}
          <b>复杂任务模型</b>。每次生成前也可以在续写页手动切换。
          <br />
          注意：模型名要以你账号实际可用为准。若报「模型名不存在」，请用上面的「拉取可用模型」确认。
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="label">正文模型（text_model）</label>
            <input
              className="input"
              list="candidate-models"
              value={form.text_model}
              onChange={(e) => update('text_model', e.target.value)}
            />
          </div>
          <div>
            <label className="label">复杂任务模型（reasoning_model）</label>
            <input
              className="input"
              list="candidate-models"
              value={form.reasoning_model}
              onChange={(e) => update('reasoning_model', e.target.value)}
            />
          </div>
        </div>
        <datalist id="candidate-models">
          {Array.from(new Set([...candidates, ...models.map((m) => m.id)])).map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>

        {validation && validation.error ? (
          <p className="text-xs text-amber-400">无法校验模型名：{validation.error}</p>
        ) : null}

        {validation && validation.issues.length > 0 ? (
          <div className="space-y-2 rounded-lg border border-rose-900 bg-rose-950/40 p-3">
            <p className="text-xs font-medium text-rose-200">
              ⚠️ 检测到模型名无效 —— 这正是拆书 / 续写失败的原因：
            </p>
            {validation.issues.map((issue) => (
              <p key={issue.field} className="text-xs text-rose-300">
                · {issue.label}「{issue.configured}」不在你的账号里
              </p>
            ))}
            <p className="text-xs text-slate-400">
              你账号可用的模型：{validation.models.join('、') || '（获取失败）'}
            </p>
            <button className="btn-primary !px-3 !py-1.5 text-xs" onClick={autofixModels}>
              一键修正为：{validation.suggestion.text_model}（正文） /{' '}
              {validation.suggestion.reasoning_model}（复杂任务）
            </button>
          </div>
        ) : null}

        {validation && validation.ok ? (
          <p className="text-xs text-emerald-400">✅ 模型名校验通过</p>
        ) : null}
      </section>

      {/* ---------- 生成参数 ---------- */}
      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-slate-300">生成参数</h3>
        <div className="grid gap-3 md:grid-cols-4">
          <div>
            <label className="label">temperature（0-2）</label>
            <input
              type="number"
              step={0.05}
              min={0}
              max={2}
              className="input"
              value={form.temperature}
              onChange={(e) => update('temperature', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">top_p（0-1）</label>
            <input
              type="number"
              step={0.05}
              min={0}
              max={1}
              className="input"
              value={form.top_p}
              onChange={(e) => update('top_p', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">max_tokens</label>
            <input
              type="number"
              min={256}
              step={256}
              className="input"
              value={form.max_tokens}
              onChange={(e) => update('max_tokens', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">检索片段数 top_k</label>
            <input
              type="number"
              min={1}
              max={30}
              className="input"
              value={form.retrieval_top_k}
              onChange={(e) => update('retrieval_top_k', Number(e.target.value))}
            />
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="label">向量模型</label>
            <input
              className="input"
              value={form.embedding_model}
              onChange={(e) => update('embedding_model', e.target.value)}
            />
            <p className="mt-1 text-[11px] text-slate-500">
              默认 BAAI/bge-small-zh-v1.5，需要 <code>pip install -r requirements-embeddings.txt</code>；
              未安装时会自动降级为内置哈希向量。改这里会导致向量库重建。
            </p>
            {systemInfo ? (
              <p
                className={`mt-1 text-[11px] ${
                  systemInfo.embedding_mode === 'sentence-transformers'
                    ? 'text-emerald-400'
                    : 'text-amber-400'
                }`}
              >
                当前实际在用：
                {systemInfo.embedding_mode === 'sentence-transformers'
                  ? `✅ 语义向量模型（${systemInfo.embedding_model}，${systemInfo.embedding_dimension} 维）`
                  : '⚠️ 字符哈希向量（未安装 sentence-transformers，检索靠字面匹配）'}
                {' · '}向量库：{systemInfo.vector_backend}
              </p>
            ) : null}
          </div>
          <div>
            <label className="label">美元兑人民币汇率</label>
            <input
              type="number"
              step={0.1}
              className="input"
              value={form.usd_to_cny}
              onChange={(e) => update('usd_to_cny', Number(e.target.value))}
            />
          </div>
        </div>
        <div>
          <label className="label">模型单价（美元 / 百万 token，用于费用估算）</label>
          <textarea
            className="input h-40 resize-y font-mono text-xs"
            value={pricingText}
            onChange={(e) => setPricingText(e.target.value)}
          />
        </div>
      </section>

      {/* ---------- 番茄 MCP ---------- */}
      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-slate-300">番茄小说 MCP</h3>

        <div>
          <label className="label">MCP 启动命令</label>
          <input
            className="input"
            placeholder="npx -y @fysh925/mcp-server-fanqie"
            value={form.fanqie_mcp_command}
            onChange={(e) => update('fanqie_mcp_command', e.target.value)}
          />
        </div>

        <div>
          <label className="label">上游数据接口地址（可选）</label>
          <input
            className="input"
            placeholder="http://101.35.133.34:5000"
            value={form.fanqie_api_base}
            onChange={(e) => update('fanqie_api_base', e.target.value)}
          />
        </div>

        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost" onClick={testFanqie}>
            检测连接
          </button>
          <button className="btn-primary" onClick={runSelftest} disabled={testing}>
            {testing ? '自检中…' : '运行完整自检'}
          </button>
        </div>

        {fanqie ? (
          <p className={`text-xs ${fanqie.available ? 'text-emerald-400' : 'text-amber-400'}`}>
            {fanqie.available ? '✅ MCP 进程正常' : '⚠️ 无法连接'}：{fanqie.message}
            {fanqie.tools.length > 0 ? `（工具：${fanqie.tools.join(', ')}）` : ''}
          </p>
        ) : null}

        {selftest ? (
          <div className="space-y-1 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
            <p className="text-xs text-slate-400">上游接口：{selftest.api_base}</p>
            {selftest.steps.map((step, index) => (
              <p key={index} className={`text-xs ${step.ok ? 'text-emerald-400' : 'text-rose-400'}`}>
                {step.ok ? '✅' : '❌'} {step.step}
                {step.detail ? <span className="text-slate-500"> — {step.detail}</span> : null}
              </p>
            ))}
            {!selftest.ok ? (
              <p className="pt-1 text-xs text-amber-400">
                提示：如果只有最后一步失败，说明 MCP 本身没问题，是它依赖的第三方接口挂了。
                这种情况请先改用「上传 TXT / EPUB」，等接口恢复后再用番茄导入。
              </p>
            ) : null}
          </div>
        ) : null}

        <p className="text-[11px] text-slate-500">
          留空则无法在首页搜索番茄小说；仍可用 TXT/EPUB 上传或手动粘贴导入。
          注意：这个 MCP 依赖第三方接口，对方服务不稳定时番茄导入会失败，属于外部原因。
        </p>
      </section>

      {/* ---------- 提示词 ---------- */}
      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-slate-300">提示词模板</h3>
        <p className="text-xs text-slate-500">
          内置在 <code>backend/app/prompts.py</code>，可在此覆盖。留空或点「恢复内置」即回到默认。
        </p>
        <PromptEditor prompts={prompts} onChanged={load} />
      </section>

      <div className="flex justify-end">
        <button className="btn-primary" onClick={save} disabled={saving}>
          {saving ? '保存中…' : '保存设置'}
        </button>
      </div>
    </div>
  )
}

// ======================================================================
function PromptEditor({ prompts, onChanged }: { prompts: PromptItem[]; onChanged: () => void }) {
  const [current, setCurrent] = useState<string>('')
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!current && prompts.length > 0) setCurrent(prompts[0].key)
  }, [prompts, current])

  const item = prompts.find((p) => p.key === current)

  useEffect(() => {
    setDraft(item?.template ?? '')
  }, [item?.key, item?.template])

  const save = async () => {
    if (!item) return
    setBusy(true)
    try {
      const res = await api.updatePrompt(item.key, draft)
      toast.success(res.is_custom ? '已保存自定义提示词' : '内容与内置一致，仍使用内置模板')
      onChanged()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const reset = async () => {
    if (!item) return
    setBusy(true)
    try {
      await api.resetPrompt(item.key)
      toast.success('已恢复内置模板')
      onChanged()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {prompts.map((prompt) => (
          <button
            key={prompt.key}
            onClick={() => setCurrent(prompt.key)}
            className={`rounded-lg px-3 py-1.5 text-xs ${
              current === prompt.key ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:bg-slate-800'
            }`}
          >
            {prompt.name}
            {prompt.is_custom ? <span className="ml-1 text-amber-400">●</span> : null}
          </button>
        ))}
      </div>

      {item ? (
        <>
          <p className="text-xs text-slate-500">
            {item.description} ·{' '}
            {item.is_custom ? <span className="text-amber-400">已自定义</span> : '使用内置'}
          </p>
          <textarea
            className="input h-72 resize-y font-mono text-xs leading-6"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="flex gap-2">
            <button className="btn-primary" onClick={save} disabled={busy}>
              保存提示词
            </button>
            <button className="btn-ghost" onClick={() => setDraft(item.builtin)}>
              查看内置原文
            </button>
            <button className="btn-ghost" onClick={reset} disabled={busy || !item.is_custom}>
              恢复内置
            </button>
          </div>
        </>
      ) : null}
    </div>
  )
}
