import logging
import inspect
import urllib

import cherrypy as cp
import routes

logger = logging.getLogger(__name__)

url_resolve_map = None


def base_url():
    return cp.config.get('base_app_url', 'https://gdg.org.ua')


def uri_builder(rparams, *args, **kwargs):
    """
    *args and **kwargs are checked for integrity with corresponding handler
    """

    params = rparams['args'].copy()
    url = rparams['url']

    ikwargs = kwargs.copy()
    iargs = list(args)

    rkwargs = {}
    rargs = []

    # Match url_for's input params to real handler's signature and put them
    # into variables holding separate URI path parts along with
    # GET key-value params
    while len(params):
        param = params.popitem(last=False)[1]
        if param.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD:
            # param goes prior to *args
            if param.name in ikwargs:
                rargs.append(ikwargs.pop(param.name))
            elif len(iargs):
                rargs.append(iargs.pop(0))
            elif param.default is inspect.Parameter.empty:
                raise TypeError
            else:
                rargs.append(param.default)
        elif param.kind == inspect.Parameter.VAR_POSITIONAL:
            # param is *args
            rargs.extend(iargs)
            iargs.clear()  # or maybe it's better to del it?
        elif param.kind == inspect.Parameter.KEYWORD_ONLY:
            # param is between (* or *args) and **kwargs
            if param.name in rkwargs:
                raise TypeError(
                    'Got multiple values for argument `{}`'.format(param.name))
            elif param.name not in ikwargs:
                if param.default is inspect.Parameter.empty:
                    raise TypeError(
                        'Missing required argument `{}`'.format(param.name))
                else:
                    rkwargs[param.name] = param.default
            else:
                rkwargs[param.name] = ikwargs.pop(param.name)
        elif param.kind == inspect.Parameter.VAR_KEYWORD:
            # param is **kwargs
            rkwargs.update(ikwargs)
            ikwargs.clear()

    # Check whether there's any params exceeding ones
    # declared into original handler
    if len(iargs):
        raise TypeError('Too many positional arguments passed!')
    elif len(ikwargs):
        raise TypeError('Too many keyword arguments passed!')

    # Build URI path string ending for concatenation with base URI
    uargs = '/'.join([urllib.parse.quote_plus(_)
                      for _ in rargs if _])
    # Build GET params list string
    ukwargs = '&'.join(['='.join([urllib.parse.quote_plus(k),
                                  urllib.parse.quote_plus(str(v))])
                        for k, v in rkwargs.items() if v])

    if uargs:
        url = '/'.join([url, uargs])

    if ukwargs:
        url = '?'.join([url, ukwargs])

    # Return final URI string
    return url


def build_url_map(force=False):
    """Builds resolve map for class-based routes
        build_url_map(force=True) is called by url map builder cherrypy plugin
    """

    def retrieve_class_routes(cls, mp, handler_cls=None):
        """
        retrieve_class_routes` builds a dictionary of method paths pointing
        to corresponding handler information

        `mp` is a mount point (script_name)
        `cls` is a class being inspected
        `handler_cls` is a reference to higher-level mount point
        """
        if handler_cls is None:
            handler_cls = '.'.join([cls.__class__.__module__,
                                    cls.__class__.__name__]).lower()
        if not mp.endswith('/'):
            mp = '/'.join([mp, ''])
        res = {}
        for method in dir(cls):
            hndlr = getattr(cls, method)
            uri = mp
            if hasattr(hndlr, '__name__') \
                    and hndlr.__name__ != 'index' \
                    and method:
                uri = ''.join([mp, method])
            if not uri:
                uri = '/'

            if inspect.ismethod(hndlr):
                if getattr(hndlr, 'exposed', False):
                    if hndlr.__name__ != method:
                        continue
                    # That's it! It's a final method

                    # (args_, varargs_, varkw_, values_) = \
                    #     inspect.getargspec(hndlr)
                    # pprint(inspect.getargspec(hndlr))
                    # Use inspect.getargspec
                    # instead of inspect.signature for Python < 3.5
                    params = inspect.signature(hndlr).parameters

                    key_cls = handler_cls
                    if hndlr.__name__ != 'index':
                        key_cls = '.'.join([handler_cls, hndlr.__name__])

                    if res.get(key_cls):
                        continue

                    res[key_cls] = {
                        'args': params,
                        'url': uri}
            elif not inspect.isfunction(hndlr) and \
                    not isinstance(hndlr, property) and \
                    not method.startswith('__'):
                # Looks like we have another class instance mounted and nested
                # import ipdb; ipdb.set_trace()
                res.update(
                    retrieve_class_routes(
                        cls=hndlr,
                        mp=(''
                            if uri.endswith('/')
                            else '/').join([uri, method]),
                        handler_cls='.'.join([handler_cls, method])))
        # TODO: handle `index` and `default` methods
        if res.get(handler_cls):
            res['.'.join([handler_cls, 'index'])] = res[handler_cls]
        return res

    global url_resolve_map
    urls = {'__routes__': {}}
    if url_resolve_map is None or force:
        for script in cp.tree.apps:
            app = cp.tree.apps[script]
            if hasattr(cp.lib, 'gctools') and \
                    isinstance(app.root, cp.lib.gctools.GCRoot):
                logger.debug('It is CherryPy garbage collector app')
                logger.debug('There are tests running probably')
                logger.debug('Skipping...')
                continue
            request_dispatcher = app.config['/'].get('request.dispatch')
            if app.root is not None:
                logger.debug('It is class-based routed app')
                logger.debug(script)
                logger.debug(app)
                urls.update(retrieve_class_routes(app.root,
                                                  mp=app.script_name))
            elif isinstance(request_dispatcher,
                            cp._cpdispatch.RoutesDispatcher):
                logger.debug('It is Routes routed app')
                logger.debug('Skipping...')
                logger.debug(script)
                logger.debug(app)
                logger.debug(request_dispatcher)
                for handler_name in request_dispatcher.controllers.keys():
                    if handler_name in urls['__routes__']:
                        logger.warn('Handler name `{}` is already in routes '
                                    'URL map. Avoid having same identifier for'
                                    ' different paths!'.format(handler_name))
                    urls['__routes__'][handler_name] = {
                        'script': app.script_name,
                        'mapper': request_dispatcher.mapper}
        url_resolve_map = urls

    return urls


def url_for_class(handler, url_args=[], url_params={}):
    app_name = __name__.split('.')[0].lower()
    handler = handler.lower()

    if handler.split('.')[0] != app_name:
        handler = '.'.join([app_name, handler])

    # TODO: handle `default` method somehow
    url_route = url_resolve_map.get(handler)
    logger.debug(url_route)
    return cp.url(uri_builder(url_route, *url_args, **url_params),
                  script_name='',
                  base=base_url())


def url_for_routes(handler, **url_params):
    try:
        routes_map = url_resolve_map['__routes__']
        _ = routes_map[handler]
        mapper = _['mapper']
        script_name = _['script']

        old_mapper = None
        if hasattr(routes.request_config(), 'mapper'):
            old_mapper = routes.request_config().mapper

        # When running tests it's empty
        # But standalone run results in WSGI env stored,
        # which has 'SCRIPT_NAME'. Hacking routes to avoid prepending it
        old_environ = None
        if hasattr(routes.request_config(), 'environ'):
            old_environ = routes.request_config().environ.copy()
            routes.request_config().environ.clear()

        old_prefix = None
        if hasattr(routes.request_config(), 'prefix'):
            old_prefix = routes.request_config().prefix

        routes.request_config().mapper = mapper
        routes_url = routes.url_for(handler, **url_params)

        # Restore everything
        if old_mapper:
            routes.request_config().mapper = old_mapper

        if old_environ:
            routes.request_config().environ = old_environ

        if old_prefix:
            routes.request_config().prefix = old_prefix
    except KeyError as ke:
        raise TypeError(
            'url_for could not find handler name {}'.format(handler)) from ke
    else:
        return cp.url(routes_url,
                      script_name=script_name,
                      base=base_url())


def url_for_cp(handler):
    if not handler.startswith('/'):
        handler = '/'.join(['', handler])
    return cp.url(handler,
                  script_name='',
                  base=base_url())


def url_for_static(handler):
    if not handler.startswith('/'):
        handler = '/'.join(['', handler])
    return cp.url(handler,
                  script_name='',  # retrieve /static from config somehow
                  base=base_url())


def url_for(handler, type_='cherrypy', *, url_args=[], url_params={}):
    '''Builds URL based on params

    Invocation examples:
        url_for('Controller.Root', type_='class-based')
        url_for('Controller.Root.auth.google', type_='class-based')
        url_for('Controller.Root.auth.logout', type_='class-based')
        url_for(
            'Controller.Root.auth.logout', type_='class-based',
            url_args=['http://test.ua/xx']
        )
        url_for(
            'Controller.Root.auth.logout', type_='class-based',
            url_args=['sdf', 'sdf2'],
            url_params={'4': 1, 'asdf': '1'}
        )

    See also:
    `src/tests/test_utils.py:7:UtilTest.test_url_for`
    '''

    # TODO: implement smarter type guessing/auto-negotiation
    if type_ == 'class-based':
        return url_for_class(handler, url_args=url_args, url_params=url_params)
    elif type_ == 'routes':
        return url_for_routes(handler, **url_params)
    elif type_ == 'static':
        return url_for_static(handler)
    else:
        return url_for_cp(handler)
