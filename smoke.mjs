import { chromium } from 'playwright-chromium'
const b = await chromium.launch({ args:['--no-sandbox'] })
const page = await (await b.newContext({ viewport:{width:1200,height:900}, deviceScaleFactor:1.5 })).newPage()
const errors = []
page.on('console', m => { if (m.type()==='error') errors.push(m.text()) })
page.on('pageerror', e => errors.push('PAGEERROR: '+e.message))
await page.goto('https://sigma.fmin.xyz/ch02_newton.html', { waitUntil:'domcontentloaded', timeout:30000 })
await page.waitForSelector('.sigma-launcher', { timeout:10000 })
await page.click('.sigma-launcher')
await page.waitForSelector('.sigma-sheet', { state:'visible', timeout:5000 })
await page.waitForTimeout(1200)
const info = await page.evaluate(() => ({
  chips: [...document.querySelectorAll('.sigma-suggest')].map(b => b.textContent),
  accent: document.querySelectorAll('.sigma-suggest-accent').length,
  model: document.querySelector('.sigma-sheet-model')?.textContent,
}))
console.log('CHIPS:', JSON.stringify(info.chips))
console.log('ACCENT chips:', info.accent, '| model label:', info.model)
console.log('CONSOLE ERRORS:', errors.length ? JSON.stringify(errors.slice(0,5)) : 'none')
await page.screenshot({ path:'/root/sigma_assistant/smoke_buttons.png', clip:{x:760,y:0,width:440,height:900} }).catch(()=>page.screenshot({path:'/root/sigma_assistant/smoke_buttons.png'}))
await b.close()
