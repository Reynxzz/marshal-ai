import { describe, expect, it } from 'vitest'
import { setCarLamps } from '#/lib/simulator/actors/car'
import { buildPlantLayout } from '../plantMap'
import { createPlantScene } from '../scene'
import {
  buildTeslaMarkers,
  clipPath,
  createRibbon,
  createTeslaCar,
  setTeslaFocus,
  TESLA,
  updateRibbon,
} from '../teslaLook'

describe('tesla look', () => {
  it('clips a path to the first metres ahead', () => {
    const path = clipPath(
      [
        { x: 0, y: 0 },
        { x: 30, y: 0 },
        { x: 30, y: 40 },
      ],
      45,
    )
    expect(path).toEqual([
      { x: 0, y: 0 },
      { x: 30, y: 0 },
      { x: 30, y: 15 },
    ])
  })

  it('draws the ribbon only along a path', () => {
    const ribbon = createRibbon()
    updateRibbon(ribbon, [{ x: 0, y: 0 }])
    expect(ribbon.visible).toBe(false)
    updateRibbon(ribbon, [
      { x: 0, y: 0 },
      { x: 10, y: 0 },
    ])
    expect(ribbon.visible).toBe(true)
    expect(ribbon.geometry.getAttribute('position').count).toBe(4)
  })

  it('lights the Tesla car through the engine lamp API and paints the followed car blue', () => {
    const car = createTeslaCar()
    setCarLamps(car, {
      headlights: false,
      brake_lights: true,
      reverse: false,
      indicator_left: false,
      indicator_right: false,
    })
    const lamps = car.userData.parts.lamps
    expect(lamps.brake_lights[0].emissiveIntensity).toBeGreaterThan(1)
    expect(lamps.headlights[0].emissiveIntensity).toBeLessThan(0.1)
    setTeslaFocus(car, true)
    expect(car.userData.parts.body.color.getHex()).toBe(TESLA.focus)
    setTeslaFocus(car, false)
    expect(car.userData.parts.body.color.getHex()).toBe(TESLA.car)
  })

  it('builds the map and a scene in the Tesla look without WebGL', () => {
    const layout = buildPlantLayout()
    expect(buildTeslaMarkers(layout).children.length).toBeGreaterThan(3)
    const plant = createPlantScene(layout, {
      style: 'tesla',
      carPath: () => [
        { x: 0, y: 0 },
        { x: 20, y: 0 },
      ],
    })
    plant.sync({
      cars: [{ vin: 'P001', x: 0, y: 0, yaw: 0, colour: '#ff0000', highlight: 'selected' }],
      props: [],
      trucks: [],
      focus: { vin: 'P001', reason: 'selected', location: 'line_end' } as never,
      focusPose: { x: 0, y: 0, yaw: 0 },
    })
    expect(plant.carIds()).toEqual(['P001'])
    expect(plant.scene.getObjectByName('ribbon').visible).toBe(true)
    plant.setOverlays(false)
    expect(plant.scene.getObjectByName('ribbon').visible).toBe(false)
    plant.dispose()
  })
})
