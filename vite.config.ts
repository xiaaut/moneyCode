import { fileURLToPath, URL } from 'node:url'
import fs from 'node:fs/promises'
import path from 'node:path'
import https from 'node:https'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

const ROOT_DIR = fileURLToPath(new URL('.', import.meta.url))
const PORTFOLIO_FILE = path.resolve(ROOT_DIR, 'data', 'portfolio.json')
const WATCHLIST_FILE = path.resolve(ROOT_DIR, 'data', 'watchlist.json')
const ROE_ROUTE = '/api/roe'

function fetchJsonByHttps(url: string) {
  return new Promise<any>((resolve, reject) => {
    https
      .get(url, (resp) => {
        const chunks: Buffer[] = []
        resp.on('data', (chunk) => chunks.push(Buffer.from(chunk)))
        resp.on('end', () => {
          try {
            const raw = Buffer.concat(chunks).toString('utf-8')
            resolve(JSON.parse(raw))
          } catch (error) {
            reject(error)
          }
        })
      })
      .on('error', reject)
  })
}

function portfolioApiPlugin() {
  const portfolioRoute = '/api/portfolio'
  const watchlistRoute = '/api/watchlist'

  const applyMiddleware = (middlewares: { use: (handler: (req: any, res: any, next: () => void) => void) => void }) => {
    middlewares.use(async (req, res, next) => {
      const pathname = String(req.url || '').split('?')[0]
      const reqUrl = new URL(String(req.url || ''), 'http://127.0.0.1')

      if (pathname === ROE_ROUTE) {
        res.setHeader('Content-Type', 'application/json; charset=utf-8')
        if (req.method !== 'GET') {
          res.statusCode = 405
          res.end(JSON.stringify({ message: 'Method Not Allowed' }))
          return
        }

        const code = String(reqUrl.searchParams.get('code') || '').trim().toUpperCase()
        if (!/^(SH|SZ)\d{6}$/.test(code)) {
          res.statusCode = 400
          res.end(JSON.stringify({ message: 'code 参数格式错误，应为 SH600519 或 SZ000725' }))
          return
        }

        try {
          const target = `https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code=${code}`
          const data = await fetchJsonByHttps(target)
          const first = Array.isArray(data?.data) ? data.data[0] : null
          res.statusCode = 200
          res.end(JSON.stringify({ roe: first?.ROEJQ ?? null }))
          return
        } catch (error: any) {
          res.statusCode = 500
          res.end(JSON.stringify({ message: error?.message || 'roe fetch error' }))
          return
        }
      }

      if (pathname !== portfolioRoute && pathname !== watchlistRoute) return next()

      const targetFile = pathname === watchlistRoute ? WATCHLIST_FILE : PORTFOLIO_FILE

      res.setHeader('Content-Type', 'application/json; charset=utf-8')

      try {
        if (req.method === 'GET') {
          let content = '[]'
          try {
            content = await fs.readFile(targetFile, 'utf-8')
          } catch (error: any) {
            if (error?.code === 'ENOENT') {
              await fs.mkdir(path.dirname(targetFile), { recursive: true })
              await fs.writeFile(targetFile, '[]', 'utf-8')
            } else {
              throw error
            }
          }
          res.statusCode = 200
          res.end(content)
          return
        }

        if (req.method === 'POST') {
          const chunks: Uint8Array[] = []
          for await (const chunk of req) chunks.push(chunk)
          const raw = Buffer.concat(chunks).toString('utf-8') || '[]'
          const parsed = JSON.parse(raw)
          if (!Array.isArray(parsed)) {
            res.statusCode = 400
            res.end(JSON.stringify({ message: 'payload 必须是数组' }))
            return
          }

          await fs.mkdir(path.dirname(targetFile), { recursive: true })
          await fs.writeFile(targetFile, `${JSON.stringify(parsed, null, 2)}\n`, 'utf-8')
          res.statusCode = 200
          res.end(JSON.stringify({ ok: true }))
          return
        }

        res.statusCode = 405
        res.end(JSON.stringify({ message: 'Method Not Allowed' }))
      } catch (error: any) {
        res.statusCode = 500
        res.end(JSON.stringify({ message: error?.message || 'server error' }))
      }
    })
  }

  return {
    name: 'portfolio-api-plugin',
    configureServer(server: { middlewares: any }) {
      applyMiddleware(server.middlewares)
    },
    configurePreviewServer(server: { middlewares: any }) {
      applyMiddleware(server.middlewares)
    }
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
    portfolioApiPlugin()
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
})
