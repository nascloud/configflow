import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import HealthCheckFields from '../src/components/HealthCheckFields.vue'
describe('health check editor', () => {
  it('is collapsed, preserves false, converts seconds to engine milliseconds', async () => {
    const wrapper = mount(HealthCheckFields, { props: { modelValue: { timeout: 5000, lazy: false, interval: 60, url: 'https://example.test/204' }, groupType: 'url-test' } })
    expect(wrapper.find('details').attributes('open')).toBeUndefined()
    expect(wrapper.text()).toContain('60')
    await wrapper.find('[data-health="timeout"]').setValue('7.25')
    expect(wrapper.emitted('update:modelValue')?.[0]?.[0]).toMatchObject({timeout: 7250, lazy: false})
  })
  it('represents decimal seconds without binary rounding artifacts', async () => {
    const wrapper = mount(HealthCheckFields, { props: { modelValue: {}, groupType: 'url-test' } })
    await wrapper.find('[data-health="timeout"]').setValue('1.001')
    expect(wrapper.emitted('update:modelValue')?.[0]?.[0]).toMatchObject({timeout:1001})
  })
  it('does not materialize legacy fields on open and only shows tolerance for url-test', () => {
    const wrapper = mount(HealthCheckFields, { props: { modelValue: {}, groupType: 'fallback' } })
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.find('[data-health="tolerance"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('未设置')
  })
})
