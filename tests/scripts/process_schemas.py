#!/usr/bin/env python3
"""
This is a preprocessor and format conversion tool for OPTIMADE schema files.
It can process individual files or entire directories and supports various
input and output formats. In particular, it adds a few preprocessor directives:

- $$inherit: reference another schema or a list of schemas to inline into the
  schema being processed, with further dictionary members being deep merged
  into the inherited schema. Non-dictionary members are replaced.

- $$keep: used alongside $$inherit to specify a list of keys to import.
  If specified, only the members specified are merged and all others discarded.
  $keep is always evaluated before $exclude, i.e., $exclude can be used to
  exclude keys inside those members that are kept.

- $$exclude: a list of keys to use alongside $$inherit to not import some members
  via inheritance. It is in particular useful when one wants to replace a
  dictionary member instead of deep merge members into it. If "/" is in the
  value, it is used as a pointer specification to descend into members.

- $$schema: is replaced by $shema with an extension added for the output format.

Usage:
  process_schemas.py source [options]

Examples:
  # Process a single file and write the output to stdout:
  process_schemas.py file.json

  # Process all files in a directory and write the output to a file:
  process_schemas.py dir -o output.json

"""

import argparse, io, codecs, os, sys, logging, traceback
from collections import OrderedDict
import urllib.parse
import urllib.request

supported_input_formats = ['json', 'yaml']
supported_output_formats = ["json", "yaml", "md"]

arguments = [
    {
        'names': ['source'],
        'help': 'The property definition file, directory or URL to process.',
        'type': str,
    },
    {
        'names': ['--refs-mode'],
        #'help': 'How to handle $ref references. Can also be set by a x-propdefs-ref-mode key alongside $ref. Also, the x-propdefs-inherit-ref key does a deep merge on the referenced definition.',
        'help': argparse.SUPPRESS,
        'choices': ["insert", "rewrite", "retain"],
        'default': "retain",
    },
    {
        'names': ['-i', '--input-format'],
        'help': 'The input format to read',
        'choices': ["auto"] + supported_input_formats,
        'default': "auto",
    },
    {
        'names': ['-f', '--output-format'],
        'help': 'The output format to generate',
        'choices': ["auto"] + supported_output_formats,
        'default': "auto",
    },
    {
        'names': ['--basedir'],
        'help': 'Base directory relative to which $$inherit referencs are resolved',
    },
    {
        'names': ['--baseid'],
        'help': 'Base id to relative to which $$inherit references are resolved',
    },
    {
        'names': ['-s', '--sub'],
        'help': 'Define a subsitution: all occurences in strings of key will be replaced by val',
        'nargs': 2, 'metavar': ('key', 'value'), 'action': 'append',
        'default': []
    },
    {
        'names': ['-o', '--output'],
        'help': 'Write the output to a file',
    },
    {
        'names': ['--remove-null'],
        'help': 'Remove keys if the value is null',
        'action': 'store_true',
        'default': False
    },
    {
        'names': ['-d', '--debug'],
        'help': 'Produce full tracebacks on error',
        'action': 'store_true',
        'default': False
    },
    {
        'names': ['-v', '--verbose'],
        'help': 'Increase verbosity of output',
        'dest': 'verbosity', 'action': 'append_const', 'const': 1,
    },
    {
        'names': ['-q', '--quiet'],
        'help': 'Decrease verbosity of output',
        'dest': 'verbosity', 'action': 'append_const', 'const': -1,
    },
    {
        'names': ['-c', '--clean-inner-schemas'],
        'help': 'Clean out inner $schema occurences',
        'action': 'store_true',
        'default': False
    },
    {
        'names': ['--schema'],
        'help': 'Add a schema to use for validation if its $id is referenced by $schema in the instance (can be given multiple times)',
        'action': 'append', 'nargs': 1
    },
    {
        'names': ['--force-schema'],
        'help': 'Force validation against the given schema regardless of precense of $schema or not in instance',
        'nargs': 1
    },

]

class ExceptionWrapper(Exception):
    """
    A class used to wrap exceptions with additional information.

    Attributes
    ----------
    debug : bool
        A flag indicating whether debug mode is enabled. (If not, a helpful message
        about how to enable a full traceback and/or more verbosity in the error
        reporting. Default is False.
    """
    debug = False
    def __init__(self,msg,e):
        """
        Initialize the ExceptionWrapper instance.

        Parameters
        ----------
        msg : str
            The message to include in the error.
        e : Exception
            The exception to wrap.
        """
        cause = e
        #while cause.__cause__:
        #    print("DECENDING CAUSE",cause)
        #    cause = cause.__cause__
        #print("FINAL CAUSE",cause,cause.__cause__)
        tb = cause.__traceback__
        tbdump = traceback.extract_tb(tb)
        #edata = " ("+str(os.path.split(tb.tb_frame.f_code.co_filename)[-1])+" line "+str(tb.tb_lineno)+")."
        if len(tbdump) > 1:
            if tbdump[0].filename == tbdump[-1].filename:
                edata = " ("+str(tbdump[0].filename)+" line:"+str(tbdump[0].lineno)+" triggered at line: "+str(tbdump[-1].lineno)+")."
            else:
                edata = " ("+str(tbdump[0].filename)+" line "+str(tbdump[0].lineno)+" triggered in: "+str(tbdump[-1].filename)+" at line "+str(tbdump[-1].lineno)+")."
        else:
            edata = " ("+str(tbdump[0].filename)+" line "+str(tbdump[0].lineno)+")."
        if isinstance(e, ExceptionWrapper):
            self.messages = [msg + edata] + e.messages
        elif type(e) == Exception:
            self.messages = [msg, str(cause) + edata]
        else:
            self.messages = [msg, type(cause).__name__+": "+str(cause) + edata]
        full_message = msg +". Error details:\n- "+("\n- ".join(self.messages[1:]))+"\n"
        if not self.debug:
            full_message += "\nAdd command line argument -d for a full traceback or one or more -v for higher verbosity."
        super().__init__(full_message)


def validate(instance, schemas={}, schema=None):
    import jsonschema
    if schema is None:
        if '$schema' in instance:
            schema_id = instance['$schema']
            if schema_id in schemas:
                schema = schemas[schema_id]
            else:
                base_schema_id, ext = os.path.splitext(schema_id)
                if base_schema_id in schemas:
                    schema = schemas[base_schema_id]
                else:
                    raise Exception("Validation: reference to unknown schema id: "+str(schema_id))
        else:
            raise Exception("Validation: validation requested but instance does not contain $schema, nor was an explicit schema given")

    try:
        jsonschema.validate(instance=instance, schema=schema, format_checker=jsonschema.FormatChecker())
    except jsonschema.ValidationError as e:
        logging.debug("Schema validation failed, full output: "+str(e))
        raise Exception("Schema validation failed: "+str(e.message)+". Error at JSON path: /"+("/".join([str(x) for x in e.path]))+".")

    except jsonschema.SchemaError as e:
        logging.debug("Invalid schema: "+str(e))
        raise Exception("Invalid schema: "+e.message)

def read_data(source, input_format='auto', preserve_order=True):
    """
    Reads data from a file or a URL and returns the parsed content.

    Parameters
    ----------
    source : str
        A string specifying the file name or the URL to fetch.
    input_format : str, optional
        The format of the input file. If set to 'auto' (default), the format will be detected automatically.

    Returns
    -------
    tuple
        A tuple containing the parsed content and its format.
    """

    logging.debug("Read data from: %s",source)

    reader = None
    try:
        parsed_url = urllib.parse.urlparse(source)
        if parsed_url.scheme in ['http', 'https', 'ftp']:
            resource = urllib.request.urlopen(source)
            charset = resource.headers.get_content_charset()
            reader = codecs.getreader(charset)(resource)
            if input_format == 'auto':
                if resource.headers.get_content_maintype() in ['application', 'text']:
                    input_format = resource.headers.get_content_subtype()
                    if input_format.startswith('x-'):
                       input_format = input_format[2:]
        else:
            base, orig_ext = os.path.splitext(parsed_url.path)
            if os.path.isabs(base):
                base = os.path.join('.',os.path.relpath(base,'/'))
            for ext in [orig_ext] + ["."+x for x in supported_input_formats]:
                logging.debug("Checking for file: %s",base+ext)
                if os.path.isfile(base+ext):
                    reader = open(base+ext, 'r')
                    if input_format == 'auto':
                        input_format = ext.lstrip(".")
                    break
            else:
                # meant to raise a proper FileNotFoundError
                reader = open(base+orig_ext, 'r')

        if input_format == "yaml":
            import yaml
            return yaml.safe_load(reader), "yaml"
        if input_format == "json":
            import json
            #if preserve_order:
            #    return json.load(reader, object_pairs_hook=OrderedDict), "json"
            #else:
            return json.load(reader), "json"
        else:
            raise Exception("Unknown input format or unable to automatically detect for: "+source+", input_format: "+str(input_format))
    except Exception as e:
        raise ExceptionWrapper("Couldn't load data from: "+str(source),e)

    finally:
        if reader is not None:
            reader.close()


def data_to_md(data, level=0):
    """
    Convert data representing OPTIMADE Property Definitions into a markdown string.

    Parameters
    ----------
    data : dict
        A dictionary containing the OPTIMADE Property Definition data.

    Returns
    -------
    str
        A string representation of the input data.
    """

    headers=["-", "=", "###", "####", "#####", "######"]

    if not "x-optimade-property" in data:
        s = ""
        for item in sorted(data.keys()):
            try:
                if isinstance(data[item], dict):
                    if level <= 2:
                        s += item + "\n"
                        s += headers[level]*len(item)+"\n\n"
                    else:
                        s += headers[level] + " " + item + "\n"
                    s += data_to_md(data[item], level=level+1)
                elif item == "$id":
                    continue
                else:
                    raise Exception("Internal error, unexpected data for data_to_md: "+str(data))
                    exit(0)
            except Exception as e:
                raise ExceptionWrapper("Could not process item: "+item,e)
            s += "\n"
        return s

    support_descs = {
        "must": "MUST be supported by all implementations, MUST NOT be :val:`null`.",
        "should": "SHOULD be supported by all implementations, i.e., SHOULD NOT be :val:`null`.",
        "may": "OPTIONAL support in implementations, i.e., MAY be :val:`null`."
    }
    query_support_descs = {
        "all mandatory" : "MUST be a queryable property with support for all mandatory filter features.",
        "equality only" : "MUST be queryable using the OPTIMADE filter language equality and inequality operators. Other filter language features do not need to be available.",
        "partial" : "MUST be a queryable property.",
        "none": "Support for queries on this property is OPTIONAL."
    }

    title = data['title']
    description_short, sep, description_details = [x.strip() for x in data['description'].partition('**Requirements/Conventions:**')]
    examples = "- " + "\n- ".join(["`"+str(x)+"`" for x in data['examples']])

    req_support_level, req_sort, req_query = ["Not specified"]*3
    req_partial_info = ""
    if 'x-optimade-requirements' in data:
        if 'support' in data['x-optimade-requirements']:
            req_support = data['x-optimade-requirements']['support']
        if 'sortable' in data['x-optimade-requirements']:
            req_sort = data['x-optimade-requirements']['sortable']
        if 'query-support' in data['x-optimade-requirements']:
            req_query = data['x-optimade-requirements']['query-support']
            if req_query == "partial":
                req_partial_info = "The following filter language features MUST be supported: "+", ".join(data['x-optimade-requirements']['query-support-operators'])

    #TODO: need to iterate through dicts, lists to get the full type
    optimade_type = data['x-optimade-type']

    s = "**Name**: "+str(title)+"\n"
    s += "**Description**: "+str(description_short)+"\n"
    s += "**Type**: "+str(optimade_type)+"\n"
    s += "**Requirements/Conventions**:\n"
    s += "- **Support**: "+support_descs[req_support]+"\n"
    s += "- **Query**: "+query_support_descs[req_query]+"\n"
    s += "- **Response**:\n"
    s += description_details+"\n"
    s += "**Examples**:\n\n"+examples
    s += "\n"

    return s


def output_str(data, output_format='json'):
    """
    Serializes key-value data into a string using the specified output format.

    Parameters
    ----------
    data : dict
        The data to be represented as a string in the specified output format.
    output_format : str, optional
        The format of the output string. Default is 'json'.

    Returns
    -------
    str
        A string representation of the input data in the specified output format.
    """

    if output_format == "json":
        import json
        return json.dumps(data, indent=4)
    elif output_format == "yaml":
        import yaml
        return yaml.dump(data)
    elif output_format == "md":
        return data_to_md(data)
    else:
        raise Exception("Unknown output format: "+str(output_format))


def inherit_to_source(ref, bases):
    """
    Convert a JSON Schema $$inherit reference to a source path.

    Parameters
    ----------
    ref : str
        A JSON reference to be converted to a source path.
    bases : dict
        A dictionary containing information about the base paths to use when
        converting the reference to a source path. Must contain the keys "id"
        and "dir".

    Returns
    -------
    str
        The source path corresponding to the input reference.

    """
    parsed_ref = urllib.parse.urlparse(ref)
    if parsed_ref.scheme in ['file', '']:
        ref = parsed_ref.path
        if os.path.isabs(ref):
            # Re-process absolute path to file path
            absref = urllib.parse.urljoin(bases['id'], ref)
            relref = absref[len(bases['id']):]
            return os.path.join(bases['dir'],relref)
        else:
            return os.path.join(bases['self'],ref)
    return ref


def recursive_replace(d, subs):
    """
    Recursively replace substrings in values of nested dictionaries that are strings.

    Parameters
    ----------
    d : dict
        The dictionary of strings to perform the replacements on.
    subs : list of tuple
        The list of key-value pairs to use for replacement. Each tuple contains
        two elements: the substring to replace and the string to replace it with.

    Returns
    -------
    dict
        A dictionary with the specified replacements.
    """
    logging.debug("Substuting strings: %s", subs)

    for key, val in d.items():
        if isinstance(val,str):
            for lhs,rhs in subs.items():
                val = val.replace(lhs,rhs)
            d[key] = val
        if isinstance(val,dict):
            recursive_replace(val, subs)
    return d


def handle_inherit(ref, mode, bases, subs, args):
    """
    Handle a single inheritance.

    Parameters
    ----------
    ref : str
        The JSON reference to be handled.
    mode : str
        The mode to use when handling the reference. Must be one of "retain",
        "rewrite", or "insert".
    bases : dict
        A dictionary containing information about the base paths to use when
        converting the reference to a source path. Must contain the keys "id",
        "self", and "dir".
    subs: dict
        dictionary of substitutions to make in strings.

    Returns
    -------
    dict or str
        If mode is "retain" or "rewrite", the function returns a
        dictionary containing the reference. If the reference mode is "insert",
        the function returns the data from the referenced file, as a dictionary
        or string.
    """

    logging.info("Handle single $$inherit: %s",ref)
    if mode == "retain":
        return { "$ref": ref }
    elif mode == "rewrite":
        base, ext = os.path.splitext(ref)
        return { "$ref": base + '.' + args.input_format }
    elif mode == "insert":
        source = inherit_to_source(ref, bases)
        data = read_data(source, args.input_format)[0]
        if subs is not None:
            return recursive_replace(data, subs)
        else:
            return data
    else:
        raise Exception("Internal error: unexpected refs_mode: "+str(refs_mode))


def merge_deep(d, other, replace=True):
    """
    Make a deep merge of the other dictionary into the first (modifying the first)

    Parameters
    ----------
    d : dict
        The dictionary to be merged into.
    other : dict
        The dictionary to merge from.
    replace : bool
        Replace items already in d
    """
    for other_key, other_val in other.items():
        val = d.get(other_key)
        if isinstance(val, dict) and isinstance(other_val, dict):
            merge_deep(val, other_val)
        elif replace or (other_key not in d):
            d[other_key] = other_val


def handle_all(data, bases, subs, args, level=0):
    """
    Recursively handles all '$$inherit' references and perform substitutions in the input data.

    Parameters
    ----------
    data : dict
        The input data to be processed for '$$inherit' references.
    bases : dict
        A dictionary containing information about the base paths to use when
        converting the reference to a source path. Must contain the keys "id",
        "self", and "dir". If not provided, the current working directory will
        be used as the base path.
    subs: dict
        dictionary of substitutions to make in strings.
    args: dict or dict-like (e.g., argument parse object)
        additional settings affecting processing
    level: int, optional
        count recursion level. Default is 0.

    Returns
    -------
    dict
        The input data with '$$inherit' references handled according to the specified mode.
    """

    logging.debug("Handling: %s",data)

    if isinstance(data, list):
        for i in range(len(data)):
            if isinstance(data[i], dict) or isinstance(data[i], list):
                data[i] = handle_all(data[i], bases, subs, args)
        return data

    elif isinstance(data, dict):

        if '$$inherit' in data:

            if not isinstance(data['$$inherit'], list):
                inherits = [data['$$inherit']]
            else:
                inherits = data['$$inherit']

            for inherit in inherits:

                inherit = data['$$inherit']
                logging.debug("Handling $$inherit preprocessor directive: %s",inherit)

                output = handle_inherit(inherit, "insert", bases, subs, args)
                if isinstance(output, dict):
                    # Handle the inherit recursively
                    newbases = bases.copy()
                    source = inherit_to_source(inherit, bases)
                    newbases['self'] = os.path.dirname(source)
                    output = handle_all(output, newbases, subs, args)

                if '$$keep' in output:
                    logging.debug("Handling $$keep preprocessor directive: %s",output['$$exclude'])
                    for key in list(output.keys()):
                        if key not in output['$$keep']:
                            del output[key]
                    del output['$$keep']

                if '$$exclude' in output:
                    logging.debug("Handling $$exclude preprocessor directive: %s",output['$$exclude'])
                    for item in output['$$exclude']:
                        pointer = re.split(r'(?<!\\)/', item)
                        loc = output
                        while len(pointer) > 1:
                            key = pointer.pop(0)
                            if key in loc:
                                loc = loc[key]
                            else:
                                raise Exception("$$exclude path pointer invalid:",item)
                        del loc[pointer[0]]

                merge_deep(data, output, replace=False)

            del data['$$inherit']

        if '$$schema' in data:
            data['$schema'] = data['$$schema']+"."+args.output_format
            del data['$$schema']

        if '$schema' in data and level > 0 and args.clean_inner_schemas:
            del data['$schema']

        for k, v in list(data.items()):
            if isinstance(v, dict) or isinstance(v, list):
                data[k] = handle_all(v, bases, subs, args)
            if args.remove_null and v is None:
                del data[k]

        return data

    else:
        raise Exception("handle: unknown data type, not dict or list: %s",type(data))


def process(source, bases, subs, args):
    """
    Processes the input file according to the specified parameters.

    Parameters
    ----------
    source : str
        The path to a file or a URL to the input to be processed.
    bases : dict, optional
        A dictionary containing information about the base paths to use when converting references
        to source paths. Must contain the keys "id" and "dir". If not provided, the current working
        directory will be used as the base path.
    subs: dict
        dictionary of substitutions to make in strings.
    args: dict or dict-like (e.g., argument parse object)
        additional settings affecting processing

    Returns
    -------
    str
        A string representation of the processed output data in the specified output format.

    """
    data, input_format = read_data(source, args.input_format)
    parsed_source = urllib.parse.urlparse(source)
    bases['self'] = os.path.dirname(parsed_source.path)

    if "$id" in data:
        id_uri = data["$id"]

        if bases['id'] is None:
            if 'dir' in bases and bases['dir'] is not None:
                prefix = os.path.commonprefix([bases['dir'], source])
            else:
                prefix = ""
            rel_source = source[len(prefix):]
            if not id_uri.endswith(rel_source):
                rel_source, ext = os.path.splitext(rel_source)
                if not id_uri.endswith(rel_source):
                    raise Exception("The $id field needs to end with: "+str(rel_source)+" but it does not: "+str(id_uri))
            bases = {'id': id_uri[:-len(rel_source)], 'dir': bases['dir'] }

    data = handle_all(data, bases, subs, args)

    return data


def process_dir(source_dir, bases, subs, args):
    """
    Processes all files in a directory and its subdirectories according to the specified parameters.

    Parameters
    ----------
    source_dir : str
        The path to the directory containing the files to be processed.
    bases : dict
        A dictionary containing information about the base paths to use when converting references
        to source paths. Must contain the keys "id" and "dir". If not provided, the current working
        directory will be used as the base path.
    subs: dict
        dictionary of substitutions to make in strings.
    args: dict or dict-like (e.g., argument parse object)
        additional settings affecting processing

    Returns
    -------
    dict
        A dictionary containing the processed data from all files in the directory and its
        subdirectories, where the keys are the file names and the values are the processed data.
    """

    alldata = {}

    for filename in os.listdir(source_dir):
        f = os.path.join(source_dir,filename)
        if os.path.isdir(f):
            logging.info("Process dir reads directory: %s",f)
            dirdata = process_dir(f, bases, subs, args)
            alldata[os.path.basename(f)] = dirdata
        elif os.path.isfile(f):
            base, ext = os.path.splitext(f)
            if ext[1:] in supported_input_formats:
                logging.info("Process dir reads file: %s",f)
                data = process(f, bases, subs, args)
                alldata.update(data)

    return alldata


if __name__ == "__main__":

    try:

        parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        for arg in arguments:
            names = arg.pop('names')
            parser.add_argument(*names, **arg)

        parser.set_defaults(verbosity=[2])

        args = parser.parse_args()
        bases = {'id':args.baseid, 'dir':args.basedir }
        subs = dict(args.sub) if len(args.sub) > 0 else None

        # Make sure verbosity is in the allowed range
        log_levels = [logging.CRITICAL, logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG]
        verbosity = min(len(log_levels) - 1, max(sum(args.verbosity), 0))
        logging.basicConfig(format='%(levelname)s: %(message)s',level=log_levels[verbosity])
        # Turn on tracebacks, etc., if verbosity is max *or* the debug flag is given
        debug = args.debug or verbosity == len(log_levels)-1
        ExceptionWrapper.debug = debug

        # Figure out output format
        if args.output_format == "auto":
            if args.output:
                base, ext = os.path.splitext(args.output)
                if ext in ["."+x for x in supported_input_formats]:
                    args.output_format = ext[1:]
                else:
                    raise Exception("Output format cannot be determined, use -f. Output file extension: "+str(ext))
            else:
                args.output_format = "json"

    except Exception as e:
        print("Internal error when parsing command line arguments: " +type(e).__name__+": "+str(e)+'.', file=sys.stderr)
        if "-d" in sys.argv:
            raise
        exit(1)

    try:
        try:
            if os.path.isdir(args.source):
                logging.info("Processing directory: %s", args.source)
                data = process_dir(args.source, bases, subs, args)
            else:
                logging.info("Processing file: %s", args.source)
                data = process(args.source, bases, subs, args)

        except Exception as e:
            raise ExceptionWrapper("Processing of input failed", e) from e

        try:
            if args.force_schema:
                schema_data, ext = read_data(args.force_schema, "json")
                validate(data, schema=args.force_schema)

            if args.schema is not None and '$schema' in data:
                schemas = {}
                for schema in args.schema:
                    schema_data, ext = read_data(schema[0], "json")
                    if '$id' in schema_data:
                        schemas[schema_data['$id']] = schema_data
                    else:
                        raise Exception("Schema provided without $id field: "+str(schema_data))
                validate(data, schemas=schemas)

        except Exception as e:
            raise ExceptionWrapper("Validation of data failed", e) from e

        try:
            logging.info("Serializing data into format: %s", args.output_format)
            outstr = output_str(data, args.output_format)
        except Exception as e:
            raise ExceptionWrapper("Serialization of data failed", e) from e

        try:
            if args.output:
                logging.info("Writing serialized output to file: %s", args.output)
                with open(args.output, "w") as f:
                    f.write(outstr)
            else:
                logging.info("Writing serialized output to stdout")
                print(outstr)

        except Exeption as e:
            raise ExceptionWrapper("Writing output data failed", e) from e

    except Exception as e:
        if debug:
            raise
        else:
            print(e)
            exit(1)