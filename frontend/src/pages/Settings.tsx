/** 设置页：LLM 网关、模型路由、剧情状态、生成参数、向量、番茄 MCP、提示词。 */
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
  Provider,
  SystemInfo,
} from '../types'

interface FormState {
  base_url: string
  text_model: string
  reasoning_model: string
  cheap_model: string
  temperature: number
  top_p: number
  max_tokens: number
  retrieval_top_k: number
  llm_timeout: number
  llm_max_retries: number
  plan_batch_size: number
  keyword_recall_enabled: boolean
  keyword_recall_limit: number
  embedding_model: string
  // 剧情状态（续写一致性）
  auto_extract_state: boolean
  state_model: string
  previous_full_chapter: boolean
  previous_tail_chars: number
  state_snapshot_budget: number
  usd_to_cny: number
  fanqie_mcp_command: string
  fanqie_api_base: string
}

const EMPTY_FORM: FormState = {
  base_url: 'https://api.deepseek.com',
  text_model: '',
  reasoning_model: '',
  cheap_model: '',
  temperature: 0.85,
  top_p: 0.95,
  max_tokens: 32768,
  retrieval_top_k: 8,
  llm_timeout: 600,
  llm_max_retries: 1,
  plan_batch_size: 5,
  keyword_recall_enabled: true,
  keyword_recall_limit: 4,
  embedding_model: 'BAAI/bge-small-zh-v1.5',
  auto_extract_state: true,
  state_model: '',
  previous_full_chapter: true,
  previous_tail_chars: 3000,
  state_snapshot_budget: 6000,
  usd_to_cny: 7.2,
  fanqie_mcp_command: '',
  fanqie_api_base: '',
}

/** 新建/编辑网关的表单 */
interface GatewayForm {
  id: number | null
  name: string
  base_url: string
  api_key: string
  text_model: string
  reasoning_model: string
  cheap_model: string
  note: string
}

const EMPTY_GATEWAY: GatewayForm = {
  id: null,
  name: '',
  base_url: '',
  api_key: '',
  text_model: '',
  reasoning_model: '',
  cheap_model: '',
  note: '',
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
  const [providers, setProviders] = useState<Provider[]>([])
  const [activeProvider, setActiveProvider] = useState<Provider | null>(null)
  const [gateway, setGateway] = useState<GatewayForm>(EMPTY_GATEWAY)
  const [gatewayOpen, setGatewayOpen] = useState(false)
  const [gatewayBusy, setGatewayBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loadingModels, setLoadingModels] = useState(false)

  const activeId = activeProvider?.id ?? null

  const load = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([api.getSettings(), api.listPrompts()])
      const settings = s.settings
      setRaw(settings)
      setCandidates(s.candidate_models)
      setProviders(s.providers || [])
      setActiveProvider(s.active_provider ?? null)
      setForm({
        base_url: settings.base_url,
        // 模型路由跟随「当前启用的网关」；没有网关时退回旧版单网关配置
        text_model: s.active_provider?.text_model || settings.text_model,
        reasoning_model: s.active_provider?.reasoning_model || settings.reasoning_model,
        cheap_model: s.active_provider?.cheap_model || '',
        temperature: settings.temperature,
        top_p: settings.top_p,
        max_tokens: settings.max_tokens,
        retrieval_top_k: settings.retrieval_top_k,
        llm_timeout: settings.llm_timeout ?? 600,
        llm_max_retries: settings.llm_max_retries ?? 1,
        plan_batch_size: settings.plan_batch_size ?? 5,
        keyword_recall_enabled: settings.keyword_recall_enabled ?? true,
        keyword_recall_limit: settings.keyword_recall_limit ?? 4,
        embedding_model: settings.embedding_model,
        auto_extract_state: settings.auto_extract_state ?? true,
        state_model: settings.state_model || '',
        previous_full_chapter: settings.previous_full_chapter ?? true,
        previous_tail_chars: settings.previous_tail_chars ?? 3000,
        state_snapshot_budget: settings.state_snapshot_budget ?? 6000,
        usd_to_cny: settings.usd_to_cny,
        fanqie_mcp_command: settings.fanqie_mcp_command,
        fanqie_api_base: settings.fanqie_api_base || '',
      })
      setPricingText(JSON.stringify(settings.pricing, null, 2))
      setPrompts(p.items)

      // 顺便校验模型名（/models 是免费接口）：配置错就直接在页面上报警，避免用户白跑一次拆书
      try {
        setValidation(await api.validateModels(s.active_provider?.id ?? null))
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

      // cheap_model 只属于网关，不是全局设置字段
      const { cheap_model, ...globalForm } = form
      const body: Record<string, unknown> = { ...globalForm, pricing }
      if (apiKey.trim()) body.api_key = apiKey.trim()

      await api.updateSettings(body)

      // 模型路由写回「当前启用的网关」；同时保留旧版字段作为兜底
      if (activeId) {
        await api.updateProvider(activeId, {
          text_model: form.text_model,
          reasoning_model: form.reasoning_model,
          cheap_model,
        })
      }

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

  // ---------------- 网关管理 ----------------
  const openGateway = (provider?: Provider) => {
    setGateway(
      provider
        ? {
            id: provider.id,
            name: provider.name,
            base_url: provider.base_url,
            api_key: '',
            text_model: provider.text_model,
            reasoning_model: provider.reasoning_model,
            cheap_model: provider.cheap_model,
            note: provider.note || '',
          }
        : EMPTY_GATEWAY,
    )
    setGatewayOpen(true)
  }

  const saveGateway = async () => {
    if (!gateway.name.trim()) {
      toast.error('请填写网关名称')
      return
    }
    setGatewayBusy(true)
    try {
      const payload = {
        name: gateway.name.trim(),
        base_url: gateway.base_url.trim(),
        text_model: gateway.text_model.trim(),
        reasoning_model: gateway.reasoning_model.trim(),
        cheap_model: gateway.cheap_model.trim(),
        note: gateway.note.trim(),
        // 留空 = 不修改已保存的 Key
        ...(gateway.api_key.trim() ? { api_key: gateway.api_key.trim() } : {}),
      }
      if (gateway.id) {
        await api.updateProvider(gateway.id, payload)
        toast.success('网关已更新')
      } else {
        await api.createProvider(payload)
        toast.success('网关已创建')
      }
      setGatewayOpen(false)
      load()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setGatewayBusy(false)
    }
  }

  const activateGateway = async (id: number) => {
    try {
      await api.activateProvider(id)
      toast.success('已切换当前网关')
      load()
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  const removeGateway = async (provider: Provider) => {
    if (!window.confirm(`确定删除网关「${provider.name}」？`)) return
    try {
      await api.deleteProvider(provider.id)
      toast.success('网关已删除')
      load()
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  const fetchModels = async () => {
    setLoadingModels(true)
    try {
      const list = await api.listModels(activeId)
      setModels(list)
      const result = await api.validateModels(activeId)
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
      const result = await api.autofixModels(activeId)
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

      {/* ---------- LLM 网关 ---------- */}
      <section className="card space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-slate-300">LLM 网关（OpenAI 兼容）</h3>
          <button className="btn-ghost !px-3 !py-1 text-xs" onClick={() => openGateway()}>
            + 新建网关
          </button>
        </div>
        <p className="rounded-lg border border-slate-800 bg-slate-950/50 p-2 text-xs text-slate-400">
          任何 OpenAI 兼容接口都能接：DeepSeek、硅基流动、火山方舟、通义、自建 Ollama / vLLM……
          填好 Base URL、API Key 和模型名即可，随时切换。API Key 只保存在本机数据库，页面只显示脱敏值。
        </p>

        <div className="space-y-2">
          {providers.length === 0 ? (
            <p className="text-xs text-amber-400">
              还没有配置网关，当前会退回下方的「旧版单网关设置」。建议先新建一个网关。
            </p>
          ) : null}
          {providers.map((provider) => (
            <div
              key={provider.id}
              className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 ${
                provider.is_active
                  ? 'border-indigo-600 bg-indigo-950/30'
                  : 'border-slate-800 bg-slate-950/40'
              }`}
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-200">{provider.name}</span>
                  {provider.is_active ? (
                    <span className="tag border border-indigo-500 bg-indigo-900/50 text-indigo-200">
                      当前启用
                    </span>
                  ) : null}
                </div>
                <p className="mt-1 truncate text-[11px] text-slate-500">
                  {provider.base_url || '（未填 Base URL）'}
                </p>
                <p className="text-[11px] text-slate-500">
                  正文 {provider.text_model || '—'} ｜ 复杂 {provider.reasoning_model || '—'} ｜ 廉价{' '}
                  {provider.cheap_model || '—'}
                </p>
                <p className="text-[11px] text-slate-600">
                  API Key：{provider.api_key_present ? `已配置 ${provider.api_key_masked}` : '未配置'}
                  {provider.note ? ` ｜ ${provider.note}` : ''}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                {!provider.is_active ? (
                  <button
                    className="btn-ghost !px-2 !py-1 text-xs"
                    onClick={() => activateGateway(provider.id)}
                  >
                    启用
                  </button>
                ) : null}
                <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => openGateway(provider)}>
                  编辑
                </button>
                <button
                  className="btn-ghost !px-2 !py-1 text-xs text-rose-300"
                  onClick={() => removeGateway(provider)}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>

        {gatewayOpen ? (
          <div className="space-y-3 rounded-lg border border-slate-700 bg-slate-950/60 p-3">
            <h4 className="text-xs font-semibold text-slate-300">
              {gateway.id ? '编辑网关' : '新建网关'}
            </h4>
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label className="label">名称</label>
                <input
                  className="input"
                  placeholder="例如 deepseek / siliconflow / 本地ollama"
                  value={gateway.name}
                  onChange={(e) => setGateway({ ...gateway, name: e.target.value })}
                />
              </div>
              <div>
                <label className="label">Base URL</label>
                <input
                  className="input"
                  placeholder="https://api.deepseek.com"
                  value={gateway.base_url}
                  onChange={(e) => setGateway({ ...gateway, base_url: e.target.value })}
                />
              </div>
              <div className="md:col-span-2">
                <label className="label">
                  API Key
                  <span className="ml-2 text-slate-500">
                    {gateway.id ? '留空表示不修改' : '本地模型（Ollama）可随便填一个'}
                  </span>
                </label>
                <input
                  type="password"
                  className="input"
                  value={gateway.api_key}
                  onChange={(e) => setGateway({ ...gateway, api_key: e.target.value })}
                />
              </div>
              <div>
                <label className="label">正文模型</label>
                <input
                  className="input"
                  value={gateway.text_model}
                  onChange={(e) => setGateway({ ...gateway, text_model: e.target.value })}
                />
              </div>
              <div>
                <label className="label">复杂任务模型</label>
                <input
                  className="input"
                  value={gateway.reasoning_model}
                  onChange={(e) => setGateway({ ...gateway, reasoning_model: e.target.value })}
                />
              </div>
              <div>
                <label className="label">廉价模型（状态抽取 / 摘要）</label>
                <input
                  className="input"
                  placeholder="留空则沿用正文模型"
                  value={gateway.cheap_model}
                  onChange={(e) => setGateway({ ...gateway, cheap_model: e.target.value })}
                />
              </div>
              <div>
                <label className="label">备注</label>
                <input
                  className="input"
                  value={gateway.note}
                  onChange={(e) => setGateway({ ...gateway, note: e.target.value })}
                />
              </div>
            </div>
            <div className="flex gap-2">
              <button className="btn-primary !px-3 !py-1.5 text-xs" onClick={saveGateway} disabled={gatewayBusy}>
                {gatewayBusy ? '保存中…' : '保存网关'}
              </button>
              <button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={() => setGatewayOpen(false)}>
                取消
              </button>
            </div>
          </div>
        ) : null}
      </section>

      {/* ---------- 模型路由 ---------- */}
      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-slate-300">
          模型路由
          {activeProvider ? (
            <span className="ml-2 text-xs font-normal text-slate-500">
              （当前网关：{activeProvider.name}）
            </span>
          ) : (
            <span className="ml-2 text-xs font-normal text-amber-400">（旧版单网关兜底）</span>
          )}
        </h3>
        <p className="rounded-lg border border-slate-800 bg-slate-950/50 p-2 text-xs text-slate-400">
          分三档：<b>正文模型</b>写正文；<b>复杂任务模型</b>负责拆书 / 大纲推演 / 伏笔检查；{' '}
          <b>廉价模型</b>负责分章摘要与剧情状态抽取（调用量最大，用最便宜的即可省很多钱）。
          每次生成前也可以在续写页手动切换。
          <br />
          模型名以网关实际可用为准。<b>三档都要确认</b>：只改了正文模型、复杂任务档还留着旧值时，
          一推演大纲 / 拆书就会报 403。若报「模型名不存在」，请用下面的「拉取可用模型」确认。
        </p>
        <div className="grid gap-3 md:grid-cols-3">
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
          <div>
            <label className="label">廉价模型（cheap_model）</label>
            <input
              className="input"
              list="candidate-models"
              placeholder="留空则沿用正文模型"
              value={form.cheap_model}
              onChange={(e) => update('cheap_model', e.target.value)}
            />
          </div>
        </div>
        <datalist id="candidate-models">
          {Array.from(new Set([...candidates, ...models.map((m) => m.id)])).map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>

        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-ghost" onClick={fetchModels} disabled={loadingModels}>
            {loadingModels ? '拉取中…' : '拉取可用模型'}
          </button>
          {models.map((model) => (
            <button
              key={model.id}
              className="tag border border-slate-700 bg-slate-800 text-slate-300 hover:border-indigo-500"
              onClick={() => {
                update('text_model', model.id)
                // 只填正文模型是最容易踩的坑：复杂任务档还留着旧值，
                // 一推演大纲就 403。所以另外两档空着或还是旧占位名时顺手填上。
                const stale = (value: string) =>
                  !value || value.toLowerCase().includes('v4.1')
                if (stale(form.reasoning_model)) update('reasoning_model', model.id)
                if (stale(form.cheap_model)) update('cheap_model', model.id)
              }}
              title="点击填入「正文模型」（复杂 / 廉价档若为空或仍是旧占位名，会一并填上）"
            >
              {model.id}
            </button>
          ))}
        </div>
        <button
          className="btn-ghost !px-2 !py-1 text-xs"
          disabled={!form.text_model}
          onClick={() => {
            update('reasoning_model', form.text_model)
            update('cheap_model', form.text_model)
            toast.info(`已把「${form.text_model}」填入复杂任务与廉价模型档，别忘了保存设置`)
          }}
        >
          把「正文模型」同步到复杂 / 廉价档
        </button>

        {validation && validation.error ? (
          <p className="text-xs text-amber-400">无法校验模型名：{validation.error}</p>
        ) : null}

        {validation && validation.issues.length > 0 ? (
          <div className="space-y-2 rounded-lg border border-rose-900 bg-rose-950/40 p-3">
            <p className="text-xs font-medium text-rose-200">
              ⚠️ 三档模型名有问题 —— 这正是拆书 / 续写失败（含 403 无权限）的原因：
            </p>
            {validation.issues.map((issue) => (
              <p key={issue.field} className="text-xs text-rose-300">
                · {issue.label}
                {issue.missing ? '还没配置' : `「${issue.configured}」不在你的网关里`}
              </p>
            ))}
            <p className="text-xs text-slate-400">
              该网关可用的模型：{validation.models.join('、') || '（获取失败）'}
            </p>
            <button className="btn-primary !px-3 !py-1.5 text-xs" onClick={autofixModels}>
              一键修正为：{validation.suggestion.text_model}（正文） /{' '}
              {validation.suggestion.reasoning_model}（复杂） / {validation.suggestion.cheap_model}（廉价）
            </button>
          </div>
        ) : null}

        {validation && validation.ok ? (
          <p className="text-xs text-emerald-400">✅ 模型名校验通过</p>
        ) : null}
      </section>

      {/* ---------- 剧情状态（续写一致性核心） ---------- */}
      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-slate-300">剧情状态（续写一致性）</h3>
        <p className="rounded-lg border border-slate-800 bg-slate-950/50 p-2 text-xs text-slate-400">
          每写完一章，系统会自动让 AI 从正文里抽取一次客观状态：
          <b>谁在哪、正在做什么、手上有什么、新知道什么、哪些线还悬着</b>。
          续写时把这些状态作为一等上下文注入，而不是只给「上一章最后 3000 字」——
          这就是解决「前后对不上」的关键。
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={form.auto_extract_state}
              onChange={(e) => update('auto_extract_state', e.target.checked)}
            />
            保存章节后自动抽取剧情状态（推荐开启）
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={form.previous_full_chapter}
              onChange={(e) => update('previous_full_chapter', e.target.checked)}
            />
            上一章按<b>整章</b>送入上下文（关闭则只送末尾若干字）
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={form.keyword_recall_enabled}
              onChange={(e) => update('keyword_recall_enabled', e.target.checked)}
            />
            关键词必召回（人名 / 物品名 / 地点名 字面兜底召回）
          </label>
          <div>
            <label className="label">上一章截断字数（关闭「整章」时生效）</label>
            <input
              type="number"
              className="input"
              value={form.previous_tail_chars}
              onChange={(e) => update('previous_tail_chars', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">状态快照字符预算</label>
            <input
              type="number"
              className="input"
              value={form.state_snapshot_budget}
              onChange={(e) => update('state_snapshot_budget', Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">关键词召回条数上限</label>
            <input
              type="number"
              className="input"
              value={form.keyword_recall_limit}
              onChange={(e) => update('keyword_recall_limit', Number(e.target.value))}
            />
          </div>
        </div>
        <p className="text-[11px] text-slate-500">
          已有书稿的剧情状态可以在「续写页 → 剧情状态」里一键补建（拆书时也会自动建立）。
        </p>
      </section>

      {/* ---------- 旧版单网关兜底 ---------- */}
      <section className="card space-y-3">
        <h3 className="text-sm font-semibold text-slate-300">
          旧版单网关兜底（没有启用任何网关时生效）
        </h3>
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
              <span className="text-[11px] text-slate-500">也可用环境变量 DEEPSEEK_API_KEY</span>
            </div>
          </div>
          <div>
            <label className="label">Base URL</label>
            <input
              className="input"
              value={form.base_url}
              onChange={(e) => update('base_url', e.target.value)}
            />
          </div>
        </div>
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

        <div className="grid gap-3 md:grid-cols-3">
          <div>
            <label className="label">单次请求超时 llm_timeout（秒）</label>
            <input
              type="number"
              min={30}
              step={30}
              className="input"
              value={form.llm_timeout}
              onChange={(e) => update('llm_timeout', Number(e.target.value) || 600)}
            />
          </div>
          <div>
            <label className="label">失败重试次数 llm_max_retries</label>
            <input
              type="number"
              min={0}
              max={5}
              className="input"
              value={form.llm_max_retries}
              onChange={(e) => update('llm_max_retries', Number(e.target.value) || 0)}
            />
          </div>
          <div>
            <label className="label">排计划每批章数 plan_batch_size</label>
            <input
              type="number"
              min={1}
              max={20}
              className="input"
              value={form.plan_batch_size}
              onChange={(e) => update('plan_batch_size', Number(e.target.value) || 5)}
            />
          </div>
        </div>
        <p className="text-[11px] text-slate-500">
          推理模型一次生成较多内容要 1~3 分钟。<b>每批章数</b>越小单次越快、进度条越早动，
          但调用次数变多；<b>超时</b>到了会被主动中断并提示，不会让任务无限挂着。
        </p>
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
