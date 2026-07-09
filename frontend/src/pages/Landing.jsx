import { useNavigate } from 'react-router-dom'
import { useCallback, useRef } from 'react'
import { ArrowRight } from 'lucide-react'


export default function Landing() {
  const nav = useNavigate()
  const orbsRef = useRef(null)

  const handleMouseMove = useCallback((e) => {
    if (!orbsRef.current) return
    const x = (e.clientX / window.innerWidth - 0.5)
    const y = (e.clientY / window.innerHeight - 0.5)
    const el = orbsRef.current
    el.children[0].style.setProperty('--mx', `${x * 40}px`)
    el.children[0].style.setProperty('--my', `${y * 40}px`)
    el.children[1].style.setProperty('--mx2', `${x * -30}px`)
    el.children[1].style.setProperty('--my2', `${y * -30}px`)
    el.children[2].style.setProperty('--mx3', `${x * 20}px`)
    el.children[2].style.setProperty('--my3', `${y * 20}px`)
    el.children[3].style.setProperty('--mx4', `${x * -15}px`)
    el.children[3].style.setProperty('--my4', `${y * -15}px`)
  }, [])

  return (
    <div className="min-h-screen bg-[#f5f5f5] font-body text-[#292524] overflow-hidden relative"
      onMouseMove={handleMouseMove}>

      {/* Gradient orbs */}
      <div ref={orbsRef} className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="orb-1 absolute top-[-10%] left-[10%] w-[600px] h-[600px] rounded-full bg-[#a7e5d3] opacity-25 blur-[120px]" />
        <div className="orb-2 absolute top-[5%] right-[5%] w-[500px] h-[500px] rounded-full bg-[#f4c5a8] opacity-20 blur-[100px]" />
        <div className="orb-3 absolute bottom-[10%] left-[30%] w-[400px] h-[400px] rounded-full bg-[#c8b8e0] opacity-20 blur-[100px]" />
        <div className="orb-4 absolute bottom-[-5%] right-[20%] w-[350px] h-[350px] rounded-full bg-[#a8c8e8] opacity-15 blur-[90px]" />
      </div>

      {/* Nav — ElevenLabs style: logo left, links center, CTA right, h-16 */}
      <nav className="relative flex items-center justify-between px-12 h-16">
        {/* Logo mark */}
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium tracking-tight">AcetiBot</span>
        </div>

        {/* Center links */}
        <div className="flex items-center gap-8">
          {['产品介绍','核心功能','常见问题'].map(label => (
            <a key={label}
              href={`#${label}`}
              className="text-[15px] font-medium text-[#4e4e4e] hover:text-[#292524] transition-colors no-underline">
              {label}
            </a>
          ))}
        </div>

        {/* CTA — removed (duplicate with hero button) */}
      </nav>

      {/* Hero — full viewport */}
      <section className="relative flex flex-col items-center justify-center text-center px-8"
        style={{ minHeight: 'calc(100vh - 64px)' }}>
        <h1 className="font-display text-[88px] leading-none font-light tracking-tight mb-6">
          智醯 AcetiBot
        </h1>
        <p className="text-xl text-[#4e4e4e] max-w-xl mx-auto mb-10 leading-relaxed">
          专注食醋垂直领域的配方辅助系统——基于工艺论文、企业专利与国家标准构建的知识库，每一条配方与工艺问题都附有可溯源的文献依据。
        </p>
        <div className="flex items-center justify-center">
          <button onClick={() => nav('/chat')}
            className="flex items-center gap-2 bg-[#292524] text-white px-8 py-3.5 rounded-full text-sm font-medium hover:bg-[#0c0a09] transition-colors">
            Try now！ <ArrowRight size={15} />
          </button>
        </div>
      </section>

      {/* 产品介绍 */}
      <section id="产品介绍" className="relative max-w-4xl mx-auto px-8 py-24 text-center">
        <h2 className="font-display text-4xl font-light tracking-tight mb-6">产品介绍</h2>
        <p className="text-lg text-[#4e4e4e] leading-relaxed max-w-2xl mx-auto">
          智醯（AcetiBot）是面向食醋酿造企业的配方优化智能体，融合食品科学、统计学与人工智能三大学科。
          系统以电子鼻、电子舌感官数据为输入，结合国家标准、企业专利与学术文献构建的垂直领域知识库，
          借助 RAG 架构输出有文献溯源的工艺参数建议与配方改良方案。
        </p>
      </section>

      <div className="border-t border-[#e7e5e4] max-w-4xl mx-auto" />

      {/* 核心功能 */}
      <section id="核心功能" className="relative max-w-4xl mx-auto px-8 py-24">
        <h2 className="font-display text-4xl font-light tracking-tight mb-10">核心功能</h2>
        <div className="grid grid-cols-2 gap-8">
          {[
            { title: '感官数据解析', desc: '上传电子鼻／舌 CSV，自动提取五维感官特征并生成雷达图' },
            { title: '知识库检索', desc: '混合语义检索与结构化过滤，精准定位国标、专利、文献' },
            { title: '配方建议生成', desc: '输出【配方建议】【工艺参数】【文献依据】三段式结果' },
            { title: '引用溯源展示', desc: '每条建议标注来源文献，上标可点击查看详情，可信度可追溯' },
          ].map(({ title, desc }) => (
            <div key={title} className="border-l-2 border-[#e7e5e4] pl-6">
              <h3 className="font-medium text-[#292524] mb-2">{title}</h3>
              <p className="text-sm text-[#777169] leading-relaxed">{desc}</p>
            </div>
          ))}
        </div>
      </section>

      <div className="border-t border-[#e7e5e4] max-w-4xl mx-auto" />

      {/* 常见问题 */}
      <section id="常见问题" className="relative max-w-4xl mx-auto px-8 py-24">
        <h2 className="font-display text-4xl font-light tracking-tight mb-10">常见问题</h2>
        <div className="space-y-8">
          {[
            { q: '系统需要真实的电子鼻/舌设备吗？', a: '是的，系统支持上传 CSV 格式的感官检测数据。也可以仅通过文字描述需求，系统将基于知识库给出配方建议。' },
            { q: '知识库的文献来源是否可信？', a: '知识库收录了 GB/T 国家标准、企业公开专利及 Food Chemistry 等 SCI 期刊文献，每条建议均标注来源，可追溯验证。' },
            { q: '生成的配方建议可以直接用于生产吗？', a: '建议作为参考依据，具体生产参数需结合企业实际工艺条件由专业工程师评估后使用。' },
          ].map(({ q, a }) => (
            <div key={q}>
              <p className="font-medium text-[#292524] mb-2">{q}</p>
              <p className="text-sm text-[#777169] leading-relaxed">{a}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Footer */}
      <footer className="relative border-t border-[#e7e5e4] px-12 py-6 flex justify-center text-xs text-[#a8a29e]">
        <span>智醯 AcetiBot © 2026</span>
      </footer>
    </div>
  )
}
