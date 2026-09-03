import { useEffect, useRef } from 'react'
import { APP_NAME } from '../../config'

const COLORS = ['#C2B067', '#D4C278', '#E8D196', '#A08340']
const PULSE_COLOR = '#F5EAB0'

interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  life: number
  maxLife: number
  size: number
  color: string
  isPulse: boolean
}

export default function BrandingHeader() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!canvasRef.current || !containerRef.current) return
    const canvas: HTMLCanvasElement = canvasRef.current
    const container: HTMLDivElement = containerRef.current
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    // Capture as non-null typed so TypeScript is happy inside nested closures
    const c: CanvasRenderingContext2D = ctx

    const dpr = window.devicePixelRatio || 1
    let animId: number
    let W = 0
    let H = 0
    const particles: Particle[] = []
    let frame = 0

    function resize() {
      const rect = container.getBoundingClientRect()
      W = rect.width
      H = rect.height
      canvas.width = W * dpr
      canvas.height = H * dpr
      canvas.style.width = `${W}px`
      canvas.style.height = `${H}px`
      c.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    window.addEventListener('resize', resize)

    function spawnParticle(isPulse: boolean) {
      const centerY = H * 0.52
      const spreadY = H * 0.38
      const speed = isPulse ? 2.8 + Math.random() * 2.2 : 0.6 + Math.random() * 1.1
      const angleDeg = isPulse ? (Math.random() * 6 - 3) : (Math.random() * 18 - 9)
      const angle = angleDeg * (Math.PI / 180)
      particles.push({
        x: isPulse ? -4 : Math.random() * W * 0.15,
        y: centerY + (Math.random() - 0.5) * spreadY,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        life: 1,
        maxLife: isPulse ? 0.55 + Math.random() * 0.25 : 0.7 + Math.random() * 0.3,
        size: isPulse ? 1.2 + Math.random() * 0.8 : 0.4 + Math.random() * 1.0,
        color: COLORS[Math.floor(Math.random() * COLORS.length)],
        isPulse,
      })
    }

    function draw() {
      c.clearRect(0, 0, W, H)
      frame++

      if (particles.length < 40 && Math.random() < 0.28) spawnParticle(false)
      if (frame % 80 < 2 && Math.random() < 0.75) spawnParticle(true)

      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i]
        p.x += p.vx
        p.y += p.vy
        p.life -= p.isPulse ? 0.022 : 0.007

        if (p.life <= 0 || p.x > W + 12) {
          particles.splice(i, 1)
          continue
        }

        const alpha = Math.min((p.life / p.maxLife) * 4, 1) * (p.isPulse ? 0.92 : 0.65)

        if (p.isPulse) {
          // Trailing beam line behind pulse dot
          const trailLen = p.vx * 10
          const grad = c.createLinearGradient(
            p.x - trailLen, p.y - p.vy * 10,
            p.x, p.y,
          )
          grad.addColorStop(0, 'rgba(0,0,0,0)')
          grad.addColorStop(1, `rgba(240,224,160,${alpha * 0.45})`)
          c.save()
          c.strokeStyle = grad
          c.lineWidth = p.size * 0.9
          c.shadowBlur = 6
          c.shadowColor = PULSE_COLOR
          c.beginPath()
          c.moveTo(p.x - trailLen, p.y - p.vy * 10)
          c.lineTo(p.x, p.y)
          c.stroke()
          c.restore()
        }

        // Particle dot with glow
        c.save()
        c.globalAlpha = alpha
        c.shadowBlur = p.isPulse ? 10 : 5
        c.shadowColor = p.isPulse ? PULSE_COLOR : p.color
        c.beginPath()
        c.arc(p.x, p.y, p.size, 0, Math.PI * 2)
        c.fillStyle = p.isPulse ? PULSE_COLOR : p.color
        c.fill()
        c.restore()
      }

      animId = requestAnimationFrame(draw)
    }

    draw()

    return () => {
      cancelAnimationFrame(animId)
      window.removeEventListener('resize', resize)
    }
  }, [])

  return (
    <div
      ref={containerRef}
      className="relative mx-3 mt-4 mb-2 rounded-xl overflow-hidden"
      style={{ background: 'rgba(194,176,103,0.04)', border: '1px solid rgba(194,176,103,0.12)' }}
    >
      <canvas ref={canvasRef} className="absolute inset-0 pointer-events-none" />
      <div className="relative z-10 px-4 py-3 select-none flex items-baseline gap-2">
        <span
          className="text-sm font-extrabold tracking-[0.3em] uppercase"
          style={{
            color: '#F0E0A0',
            textShadow: '0 0 12px rgba(240,224,160,0.6)',
          }}
        >
          {APP_NAME}
        </span>
        <span className="text-[10px] font-medium tracking-widest text-[#7A6030]">
          v0.1
        </span>
      </div>
    </div>
  )
}
