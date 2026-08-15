import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'A2A Settlement',
  tagline: 'The settlement layer for autonomous agent commerce',
  favicon: 'img/favicon.ico',

  future: {
    v4: true,
  },

  url: 'https://docs.a2a-settlement.org',
  baseUrl: '/',

  organizationName: 'a2a-settlement',
  projectName: 'a2a-settlement',

  onBrokenLinks: 'throw',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/a2a-settlement/a2a-settlement/tree/main/docs-site/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/a2a-settlement-social.png',
    colorMode: {
      defaultMode: 'dark',
      respectPrefersColorScheme: true,
    },
    announcementBar: {
      id: 'zenodo-dois',
      content:
        'NIST comments archived on Zenodo: <a target="_blank" rel="noopener noreferrer" href="https://doi.org/10.5281/zenodo.21745191">CAISI DOI</a> · <a target="_blank" rel="noopener noreferrer" href="https://doi.org/10.5281/zenodo.21745274">NCCoE DOI</a>',
      backgroundColor: '#1a8870',
      textColor: '#ffffff',
      isCloseable: true,
    },
    navbar: {
      title: 'A2A Settlement',
      logo: {
        alt: 'A2A Settlement',
        src: 'img/logo.svg',
      },
      items: [
        { to: '/docs/agent-settlement/', label: 'Concepts', position: 'left' },
        { to: '/docs/spec/', label: 'Standard', position: 'left' },
        { to: '/docs/comparisons/ap2-vs-a2ase', label: 'Interoperability', position: 'left' },
        { to: '/docs/conformance/', label: 'Conformance', position: 'left' },
        { to: '/docs/federation/', label: 'Federation', position: 'left' },
        { to: '/docs/integrations/', label: 'Implementations', position: 'left' },
        { to: '/docs/architecture/nist-compliance', label: 'Security', position: 'left' },
        { to: '/docs/standards/', label: 'Standards', position: 'left' },
        {
          href: 'https://sandbox.a2a-settlement.org',
          label: 'Run a Settlement',
          position: 'right',
        },
        {
          href: 'https://github.com/a2a-settlement/a2a-settlement',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Documentation',
          items: [
            { label: 'What is Agent Settlement?', to: '/docs/agent-settlement/' },
            { label: 'Introduction', to: '/docs/intro' },
            { label: 'Specification', to: '/docs/spec/' },
            { label: 'Standards & Provenance', to: '/docs/standards/' },
            { label: 'API Reference', to: '/docs/api/' },
          ],
        },
        {
          title: 'Ecosystem',
          items: [
            { label: 'a2a-settlement', href: 'https://github.com/a2a-settlement/a2a-settlement' },
            { label: 'settlement-conformance', href: 'https://github.com/a2a-settlement/settlement-conformance' },
            { label: 'a2a-federation-rfc', href: 'https://github.com/a2a-settlement/a2a-federation-rfc' },
            { label: 'otel-agent-provenance', href: 'https://github.com/a2a-settlement/otel-agent-provenance' },
            { label: 'mcp-trust-gateway', href: 'https://github.com/a2a-settlement/mcp-trust-gateway' },
            { label: 'a2a-settlement-auth', href: 'https://github.com/a2a-settlement/a2a-settlement-auth' },
            { label: 'a2a-settlement-mediator', href: 'https://github.com/a2a-settlement/a2a-settlement-mediator' },
            { label: 'adk-a2a-settlement', href: 'https://github.com/a2a-settlement/adk-a2a-settlement' },
            { label: 'crewai-a2a-settlement', href: 'https://github.com/a2a-settlement/crewai-a2a-settlement' },
          ],
        },
        {
          title: 'More',
          items: [
            { label: 'Main Site', href: 'https://a2a-settlement.org' },
            { label: 'Run a Settlement', href: 'https://sandbox.a2a-settlement.org' },
            { label: 'SettleBridge (product)', href: 'https://settlebridge.ai' },
            { label: 'GitHub Org', href: 'https://github.com/a2a-settlement' },
            { label: 'NIST / Standards', to: '/docs/standards/' },
            { label: 'CAISI DOI', href: 'https://doi.org/10.5281/zenodo.21745191' },
            { label: 'NCCoE DOI', href: 'https://doi.org/10.5281/zenodo.21745274' },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} TruthSetter LLC / A2A Settlement. MIT License.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
