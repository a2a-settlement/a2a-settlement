import type {ReactNode} from 'react';
import clsx from 'clsx';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

type FeatureItem = {
  title: string;
  Svg: React.ComponentType<React.ComponentProps<'svg'>>;
  description: ReactNode;
};

const FeatureList: FeatureItem[] = [
  {
    title: 'Payments',
    Svg: require('@site/static/img/layer-payments.svg').default,
    description: (
      <>
        How value moves — rails, tokens, micropayments. A2A-SE sits above or
        alongside payment rails; it does not replace them.
      </>
    ),
  },
  {
    title: 'Authorization',
    Svg: require('@site/static/img/layer-authorization.svg').default,
    description: (
      <>
        Whether an agent may spend — limits, policies, delegation. Complements
        AP2 and OAuth settlement scopes.
      </>
    ),
  },
  {
    title: 'Settlement',
    Svg: require('@site/static/img/layer-settlement.svg').default,
    description: (
      <>
        Whether the economic obligation was satisfied and what happens to
        committed value — escrow, verification, release/refund, finality.
      </>
    ),
  },
];

function Feature({title, Svg, description}: FeatureItem) {
  return (
    <div className={clsx('col col--4')}>
      <div className="text--center">
        <Svg className={styles.featureSvg} role="img" title={title} />
      </div>
      <div className="text--center padding-horiz--md">
        <Heading as="h3">{title}</Heading>
        <p>{description}</p>
      </div>
    </div>
  );
}

export default function HomepageFeatures(): ReactNode {
  return (
    <section className={styles.features}>
      <div className="container">
        <div className="row">
          {FeatureList.map((props, idx) => (
            <Feature key={idx} {...props} />
          ))}
        </div>
      </div>
    </section>
  );
}
